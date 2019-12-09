import logging
import struct
import subprocess
import tempfile
import time
import typing
from pathlib import Path

import numpy as np
from kaldi_speech.nnet3 import KaldiNNet3OnlineModel, KaldiNNet3OnlineDecoder

from .const import Transcriber, Transcription

_LOGGER = logging.getLogger(__name__)

# -----------------------------------------------------------------------------


class KaldiExtensionTranscriber(Transcriber):
    """Speech to text with Kaldi nnet3 Python extension."""

    def __init__(self, model_dir: Path, graph_dir: Path):
        self.model_dir = model_dir
        self.graph_dir = graph_dir
        self.model: typing.Optional[KaldiNNet3OnlineModel] = None
        self.decoder: typing.Optional[KaldiNNet3OnlineDecoder] = None

    def load_decoder(self):
        """Load Kaldi decoder if not already loaded."""
        if (self.model is None) or (self.decoder is None):
            # Load model/decoder
            self.model, self.decoder = self.get_model_decoder()

    def transcribe_wav(self, wav_data: bytes) -> typing.Optional[Transcription]:
        """Speech to text from WAV data."""
        self.load_decoder()
        assert self.decoder

        _LOGGER.debug("Decoding %s byte(s)", len(wav_data))
        start_time = time.perf_counter()
        with io.BytesIO(wav_data) as wav_buffer:
            with wave.open(wav_buffer, "rb") as wav_file:
                sample_rate = wav_file.getframerate()
                num_frames = wav_file.getnframes()
                wav_duration = num_frames / float(sample_rate)

                frames = wav_file.readframes(num_frames)
                samples = struct.unpack_from("<%dh" % num_frames, frames)

                # Decode
                success = self.decoder.decode(
                    sample_rate, np.array(samples, dtype=np.float32), True
                )

                if success:
                    text, likelihood = self.decoder.get_decoded_string()
                    transcribe_seconds = time.perf_counter() - start_time

                    return Transcription(
                        text=text.strip(),
                        likelihood=likelihood,
                        transcribe_seconds=transcribe_seconds,
                        wav_seconds=wav_duration,
                    )

                # Failure
                return None

    def get_model_decoder(
        self
    ) -> Tuple[KaldiNNet3OnlineModel, KaldiNNet3OnlineDecoder]:
        """Create nnet3 model/decoder using Python extension."""
        _LOGGER.debug(
            "Loading nnet3 model at %s (graph=%s)", self.model_dir, self.graph_dir
        )

        model = KaldiNNet3OnlineModel(str(self.model_dir), str(self.graph_dir))

        _LOGGER.debug("Creating decoder")
        decoder = KaldiNNet3OnlineDecoder(model)
        _LOGGER.debug("Kaldi decoder loaded")

        return model, decoder


# -----------------------------------------------------------------------------


class KaldiCommandLineTranscriber(Transcriber):
    """Speech to text with external Kaldi scripts."""

    def __init__(self, model_type: KaldiModelType, model_dir: Path, graph_dir: Path):
        self.model_type = model_type
        self.model_dir = model_dir
        self.graph_dir = graph_dir

    def transcribe_wav(self, wav_data: bytes) -> typing.Optional[Transcription]:
        """Speech to text from WAV data."""
        kaldi_cmd = [
            "kaldi-decode",
            "--model-type",
            str(self.model_type),
            "--model-dir",
            str(self.model_dir),
            "--graph-dir",
            str(self.graph_dir),
        ]

        _LOGGER.debug(kaldi_cmd)

        with tempfile.NamedTemporaryFile(suffix=".wav", mode="wb") as temp_file:
            temp_file.write(wav_data)

            # Rewind
            temp_file.seek(0)

            kaldi_proc = subprocess.Popen(
                kaldi_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                universal_newlines=True,
            )

            # Write path to WAV file
            print(temp_file.name, file=kaldi_proc.stdin)

            # Get result back as JSON
            result_json, _ = kaldi_proc.communicate()
            result = json.loads(result_json)

            # Empty string indicates failure
            text = str(result.get("text", ""))
            if text:
                # Success
                return Transcription(
                    text=text.strip(),
                    likelihood=float(result.get("likelihood", 0)),
                    transcribe_seconds=float(result.get("transcribe_seconds", 0)),
                    wav_seconds=float(result.get("wav_seconds", 0)),
                )

            # Failure
            return None