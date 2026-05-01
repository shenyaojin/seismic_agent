"""
SEGYLoaderTool: Decompress .segy.tar.gz files and load velocity models.

Populates ctx.velocity_models with:
  "vp"  → 2-D numpy array  [nz, nx]  (m/s)
  "vs"  → 2-D numpy array  [nz, nx]  (m/s, if available)
  "dx"  → float  sample interval in x (m)
  "dz"  → float  sample interval in z (m)
"""
from __future__ import annotations

import tarfile
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np

try:
    import segyio
    _SEGYIO_OK = True
except ImportError:
    _SEGYIO_OK = False

from .base import SeismicTool, ToolContext, ToolResult


_VELOCITY_RANGE = (100.0, 8000.0)  # physically plausible seismic velocity (m/s)


def _load_segy(path: Path) -> Optional[np.ndarray]:
    """Load a SEGY file and return a 2-D float32 array [traces, samples].

    Validates that values fall within a physically plausible velocity range.
    Returns None if the data looks corrupted (e.g. IBM-float endianness issue).
    """
    if not _SEGYIO_OK:
        raise RuntimeError("segyio is not installed.")
    with segyio.open(str(path), "r", ignore_geometry=True) as f:
        data = np.array([f.trace[i] for i in range(f.tracecount)], dtype=np.float32)

    # Sanity check: velocity data should be in [100, 8000] m/s
    valid = data[np.isfinite(data)]
    if valid.size == 0:
        return None
    if valid.max() > _VELOCITY_RANGE[1] * 10 or valid.min() < -1.0:
        # Likely endianness / format corruption; skip
        return None
    return data


def _extract_and_load(tar_gz: Path, tmpdir: Path) -> Optional[np.ndarray]:
    with tarfile.open(tar_gz, "r:gz") as tar:
        tar.extractall(tmpdir)
    segy_files = list(tmpdir.glob("*.segy")) + list(tmpdir.glob("*.sgy"))
    if not segy_files:
        return None
    return _load_segy(segy_files[0])


class SEGYLoaderTool(SeismicTool):
    """
    Loads Vp and Vs velocity models from SEGY (or compressed SEGY) files.

    Heuristic file routing:
      - filename contains "MODEL_P" or "Vp"  → P-wave velocity
      - filename contains "MODEL_S" or "Vs"  → S-wave velocity
    """

    def __init__(self, dx: float = 1.25, dz: float = 1.25):
        super().__init__("SEGYLoader")
        self.dx = dx
        self.dz = dz

    def run(self, ctx: ToolContext) -> ToolResult:
        if not ctx.files:
            return ToolResult(False, self.name, "No input files provided.", error="Empty file list")

        loaded: dict = {"dx": self.dx, "dz": self.dz}
        errors = []

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            # Prefer MODEL_* files over bare Vp/Vs files (better data quality)
            def _priority(p: Path) -> int:
                return 0 if p.name.lower().startswith("model_") else 1

            sorted_files = sorted(ctx.files, key=_priority)

            for f in sorted_files:
                name_lower = f.name.lower()

                # Determine which model this file contains
                is_vp = ("vp" in name_lower or "p-wave" in name_lower or "model_p" in name_lower)
                is_vs = ("vs" in name_lower or "s-wave" in name_lower or "model_s" in name_lower)

                if not (is_vp or is_vs):
                    continue

                # Skip if we already have a valid model for this type
                key = "vp" if is_vp else "vs"
                if key in loaded:
                    self.logger.info(f"Skipping {f.name} ({key} already loaded)")
                    continue

                self.logger.info(f"Loading {'Vp' if is_vp else 'Vs'} from {f.name}")
                try:
                    if f.suffix.lower() in {".gz", ".tgz"}:
                        sub_tmp = tmp_path / f.stem
                        sub_tmp.mkdir(exist_ok=True)
                        data = _extract_and_load(f, sub_tmp)
                    else:
                        data = _load_segy(f)

                    if data is not None:
                        key = "vp" if is_vp else "vs"
                        loaded[key] = data
                        self.logger.info(f"  → shape {data.shape}, range [{data.min():.0f}, {data.max():.0f}] m/s")
                except Exception as e:
                    errors.append(f"{f.name}: {e}")
                    self.logger.error(f"Failed to load {f.name}: {e}")

        if "vp" not in loaded and "vs" not in loaded:
            return ToolResult(False, self.name, "No velocity data loaded.",
                              error="; ".join(errors) if errors else "No matching files")

        ctx.velocity_models = loaded
        ctx.metrics["velocity_files_loaded"] = [k for k in ("vp", "vs") if k in loaded]

        summary = (
            f"Loaded {', '.join(ctx.metrics['velocity_files_loaded']).upper()} models. "
            f"Grid spacing: {self.dx} m. "
        )
        if "vp" in loaded:
            vp = loaded["vp"]
            summary += f"Vp shape: {vp.shape}, range: [{vp.min():.0f}, {vp.max():.0f}] m/s. "
        if "vs" in loaded:
            vs = loaded["vs"]
            summary += f"Vs shape: {vs.shape}, range: [{vs.min():.0f}, {vs.max():.0f}] m/s."

        return ToolResult(True, self.name, summary, data=loaded)
