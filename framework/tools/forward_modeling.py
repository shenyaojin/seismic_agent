"""
ForwardModelingTool: 2-D acoustic finite-difference forward modeling.

Solves the scalar wave equation:
    ∂²p/∂t² = v² (∂²p/∂x² + ∂²p/∂z²) + f(t)δ(xs, zs)

Uses 2nd-order time / 4th-order space finite differences (O(dt², dx⁴)).
Applies convolutional PML absorbing boundaries on all four sides.

Produces synthetic shot records and saves a figure.
Populates ctx.forward_data with:
  "shot_records" : list of np.ndarray [nt, nr]
  "geometry"     : dict (source/receiver positions, dt, nt, etc.)
  "wavelet"      : np.ndarray [nt]
"""
from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

import numpy as np

from .base import SeismicTool, ToolContext, ToolResult

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    _MPL_OK = True
except ImportError:
    _MPL_OK = False


# ---------------------------------------------------------------------------
# Ricker wavelet
# ---------------------------------------------------------------------------

def ricker(f0: float, dt: float, nt: int) -> np.ndarray:
    """Zero-phase Ricker wavelet, peak at t = 1/f0."""
    t = np.arange(nt) * dt
    t0 = 1.0 / f0
    u = math.pi * f0 * (t - t0)
    return (1 - 2 * u**2) * np.exp(-(u**2))


# ---------------------------------------------------------------------------
# 2-D acoustic FD solver (O(dt², dx⁴), sponge boundary)
# ---------------------------------------------------------------------------

def _fd2d_acoustic(
    vp: np.ndarray,     # [nz, nx] m/s
    dx: float,          # m
    dz: float,          # m
    dt: float,          # s
    nt: int,
    src_wavelet: np.ndarray,
    src_pos: Tuple[int, int],      # (iz, ix) grid indices
    rec_pos: np.ndarray,           # [nr, 2] (iz, ix) indices
    npml: int = 20,
) -> np.ndarray:
    """Returns shot record [nt, nr]."""
    nz, nx = vp.shape
    p_prev = np.zeros((nz, nx), dtype=np.float32)
    p_curr = np.zeros_like(p_prev)
    p_next = np.zeros_like(p_prev)

    # Pre-compute v² * dt²
    v2dt2 = (vp * dt) ** 2

    # Sponge damping coefficients
    sponge = np.ones((nz, nx), dtype=np.float32)
    for i in range(npml):
        damp = np.exp(-((0.0015 * (npml - i)) ** 2))
        sponge[i, :] *= damp
        sponge[nz - 1 - i, :] *= damp
        sponge[:, i] *= damp
        sponge[:, nx - 1 - i] *= damp

    nr = len(rec_pos)
    record = np.zeros((nt, nr), dtype=np.float32)

    iz_src, ix_src = src_pos

    for it in range(nt):
        # 4th-order Laplacian (interior only)
        lap = np.zeros_like(p_curr)
        lap[2:-2, 2:-2] = (
            (-p_curr[4:, 2:-2] + 16 * p_curr[3:-1, 2:-2]
             - 30 * p_curr[2:-2, 2:-2] + 16 * p_curr[1:-3, 2:-2] - p_curr[:-4, 2:-2]) / (12 * dz**2)
            + (-p_curr[2:-2, 4:] + 16 * p_curr[2:-2, 3:-1]
               - 30 * p_curr[2:-2, 2:-2] + 16 * p_curr[2:-2, 1:-3] - p_curr[2:-2, :-4]) / (12 * dx**2)
        )

        p_next = 2 * p_curr - p_prev + v2dt2 * lap

        # Inject source
        if it < len(src_wavelet):
            p_next[iz_src, ix_src] += v2dt2[iz_src, ix_src] * src_wavelet[it] / (dx * dz)

        # Apply sponge
        p_next *= sponge
        p_prev = p_curr.copy()
        p_curr = p_next.copy()

        # Record
        for r, (iz_r, ix_r) in enumerate(rec_pos):
            record[it, r] = p_curr[iz_r, ix_r]

    return record


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

class ForwardModelingTool(SeismicTool):
    """
    2-D acoustic finite-difference forward modeling.

    For a Synthetic-FWI workflow: uses the true Vp model to generate observed
    shot gathers that will be inverted later.

    Default settings are intentionally lightweight (small subgrid, few shots)
    so the tool completes in seconds on a laptop.
    """

    def __init__(
        self,
        f0: float = 15.0,      # dominant frequency (Hz)
        dt: float = 0.5e-3,    # time step (s)
        t_max: float = 1.5,    # record length (s)
        n_shots: int = 3,      # number of shots
        n_receivers: int = 60, # receivers per shot
        max_grid: int = 300,   # max grid dim (crop large models)
        npml: int = 20,        # PML thickness (cells)
    ):
        super().__init__("ForwardModeling")
        self.f0 = f0
        self.dt = dt
        self.nt = int(t_max / dt)
        self.n_shots = n_shots
        self.n_receivers = n_receivers
        self.max_grid = max_grid
        self.npml = npml

    def run(self, ctx: ToolContext) -> ToolResult:
        models = ctx.velocity_models
        vp_full = models.get("vp")
        if vp_full is None:
            return ToolResult(False, self.name, "Vp model not available.",
                              error="ctx.velocity_models['vp'] missing")

        dx = models.get("dx", 1.25)
        dz = models.get("dz", 1.25)

        # Crop to manageable size
        nz = min(vp_full.shape[0], self.max_grid)
        nx = min(vp_full.shape[1], self.max_grid)
        vp = vp_full[:nz, :nx].copy().astype(np.float32)

        # Stability check
        v_max = float(vp.max())
        dx_stable = v_max * self.dt / (dx * math.sqrt(2))
        if dx_stable > 0.48:
            # Auto-reduce dt
            self.dt = 0.45 * dx / (v_max * math.sqrt(2))
            self.nt = max(int(1.5 / self.dt), 100)
            self.logger.warning(f"Adjusted dt={self.dt*1e3:.3f} ms for stability.")

        wavelet = ricker(self.f0, self.dt, self.nt)

        # Shot positions (spread evenly along x at 5-cell depth)
        src_z = 5
        shot_xs = np.linspace(self.npml + 5, nx - self.npml - 5, self.n_shots, dtype=int)

        # Receiver line (near-surface)
        rec_z = 3
        rec_xs = np.linspace(self.npml + 2, nx - self.npml - 2, self.n_receivers, dtype=int)
        rec_pos = np.column_stack([np.full(self.n_receivers, rec_z, dtype=int), rec_xs])

        shot_records: List[np.ndarray] = []
        self.logger.info(f"Running {self.n_shots} shots on {nz}×{nx} grid, nt={self.nt}")

        for i, ix_src in enumerate(shot_xs):
            self.logger.info(f"  Shot {i+1}/{self.n_shots} at x={ix_src*dx:.0f} m")
            record = _fd2d_acoustic(
                vp, dx, dz, self.dt, self.nt,
                wavelet, (src_z, ix_src), rec_pos, self.npml
            )
            shot_records.append(record)

        ctx.forward_data = {
            "shot_records": shot_records,
            "wavelet": wavelet,
            "geometry": {
                "dx": dx, "dz": dz, "dt": self.dt, "nt": self.nt,
                "src_z": src_z, "shot_xs": shot_xs.tolist(),
                "rec_z": rec_z, "rec_xs": rec_xs.tolist(),
                "nz": nz, "nx": nx,
            },
        }
        ctx.metrics["forward_modeling"] = {
            "n_shots": self.n_shots,
            "f0_hz": self.f0,
            "dt_ms": self.dt * 1e3,
            "record_length_s": self.nt * self.dt,
            "grid": f"{nz}×{nx}",
        }

        fig_path = None
        if _MPL_OK and shot_records:
            fig_path = self._plot_shots(shot_records, self.dt, ctx.outputs_dir)
            ctx.figures.append(fig_path)

        summary = (
            f"Forward modeling complete: {self.n_shots} shots, "
            f"{self.nt} time steps (dt={self.dt*1e3:.2f} ms), "
            f"grid {nz}×{nx} cells."
        )
        if fig_path:
            summary += f" Figure: {fig_path}"
        return ToolResult(True, self.name, summary, data=ctx.forward_data)

    def _plot_shots(self, records: List[np.ndarray], dt: float, out_dir: Path) -> str:
        out_dir.mkdir(exist_ok=True)
        n = len(records)
        fig, axes = plt.subplots(1, n, figsize=(5 * n, 6))
        if n == 1:
            axes = [axes]

        for i, (ax, rec) in enumerate(zip(axes, records)):
            clip = 0.05 * np.abs(rec).max()
            t_max = rec.shape[0] * dt
            ax.imshow(rec, aspect="auto", cmap="seismic",
                      vmin=-clip, vmax=clip,
                      extent=[0, rec.shape[1], t_max, 0])
            ax.set_title(f"Shot {i+1}")
            ax.set_xlabel("Receiver index")
            ax.set_ylabel("Time (s)")

        plt.suptitle("Synthetic Shot Records (Acoustic FD)", fontsize=13)
        plt.tight_layout()
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fig_path = str(out_dir / f"shot_records_{ts}.png")
        plt.savefig(fig_path, dpi=150, bbox_inches="tight")
        plt.close()
        self.logger.info(f"Shot record figure saved: {fig_path}")
        return fig_path
