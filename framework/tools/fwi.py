"""
FWITool: Gradient-based Full Waveform Inversion (acoustic, 2-D).

Algorithm (adjoint-state method):
  1. Perturb the true Vp model to create an initial model (smooth + bias)
  2. For each iteration:
     a. Forward propagate with current model → synthetic data
     b. Compute data residual: Δd = d_syn - d_obs
     c. Back-propagate residual (time-reversed) → adjoint wavefield
     d. Cross-correlate forward & adjoint → gradient ∇J w.r.t. v²
     e. Update model: m ← m - α * ∇J  (steepest descent)
  3. Report convergence (normalised misfit vs iteration)

Requires ctx.forward_data (populated by ForwardModelingTool) and
         ctx.velocity_models['vp'].

Populates ctx.inversion_results with:
  "recovered_vp" : np.ndarray  [nz, nx]
  "initial_vp"   : np.ndarray  [nz, nx]
  "residuals"    : list of float  (normalised L2 misfit per iteration)
"""
from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path
from typing import List

import numpy as np
from scipy.ndimage import gaussian_filter

from .base import SeismicTool, ToolContext, ToolResult
from .forward_modeling import _fd2d_acoustic, ricker

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    _MPL_OK = True
except ImportError:
    _MPL_OK = False


def _compute_gradient(
    vp_model: np.ndarray,
    dx: float, dz: float, dt: float, nt: int,
    wavelet: np.ndarray,
    src_z: int, ix_src: int,
    rec_pos: np.ndarray,
    d_obs: np.ndarray,    # [nt, nr]
    npml: int = 20,
) -> tuple[float, np.ndarray]:
    """
    Returns (misfit, gradient) for one shot.
    gradient has same shape as vp_model.
    """
    nz, nx = vp_model.shape

    # ---- Forward pass (save snapshots for cross-correlation) ----
    p_prev = np.zeros((nz, nx), dtype=np.float32)
    p_curr = np.zeros_like(p_prev)
    v2dt2  = (vp_model * dt) ** 2

    sponge = np.ones((nz, nx), dtype=np.float32)
    for i in range(npml):
        d = np.exp(-((0.0015 * (npml - i)) ** 2))
        sponge[i, :] *= d; sponge[nz-1-i, :] *= d
        sponge[:, i] *= d; sponge[:, nx-1-i] *= d

    # Store every 5th snapshot to limit memory
    stride = 5
    snapshots: List[np.ndarray] = []
    d_syn = np.zeros_like(d_obs)

    for it in range(nt):
        lap = np.zeros_like(p_curr)
        lap[2:-2, 2:-2] = (
            (-p_curr[4:, 2:-2] + 16*p_curr[3:-1, 2:-2]
             - 30*p_curr[2:-2, 2:-2] + 16*p_curr[1:-3, 2:-2] - p_curr[:-4, 2:-2]) / (12*dz**2)
            + (-p_curr[2:-2, 4:] + 16*p_curr[2:-2, 3:-1]
               - 30*p_curr[2:-2, 2:-2] + 16*p_curr[2:-2, 1:-3] - p_curr[2:-2, :-4]) / (12*dx**2)
        )
        p_next = 2*p_curr - p_prev + v2dt2 * lap
        if it < len(wavelet):
            p_next[src_z, ix_src] += v2dt2[src_z, ix_src] * wavelet[it] / (dx*dz)
        p_next *= sponge
        p_prev = p_curr.copy()
        p_curr = p_next.copy()

        for r, (iz_r, ix_r) in enumerate(rec_pos):
            d_syn[it, r] = p_curr[iz_r, ix_r]

        if it % stride == 0:
            snapshots.append(p_curr.copy())

    # ---- Residual ----
    residual = d_syn - d_obs
    misfit = 0.5 * float(np.sum(residual**2))

    # ---- Adjoint (time-reversed) back-propagation ----
    q_prev = np.zeros((nz, nx), dtype=np.float32)
    q_curr = np.zeros_like(q_prev)
    gradient = np.zeros((nz, nx), dtype=np.float32)

    snap_idx = len(snapshots) - 1  # pointer for forward snapshots

    for it in range(nt - 1, -1, -1):
        # Inject adjoint source (residual)
        for r, (iz_r, ix_r) in enumerate(rec_pos):
            q_curr[iz_r, ix_r] += residual[it, r] * dt

        lap_q = np.zeros_like(q_curr)
        lap_q[2:-2, 2:-2] = (
            (-q_curr[4:, 2:-2] + 16*q_curr[3:-1, 2:-2]
             - 30*q_curr[2:-2, 2:-2] + 16*q_curr[1:-3, 2:-2] - q_curr[:-4, 2:-2]) / (12*dz**2)
            + (-q_curr[2:-2, 4:] + 16*q_curr[2:-2, 3:-1]
               - 30*q_curr[2:-2, 2:-2] + 16*q_curr[2:-2, 1:-3] - q_curr[2:-2, :-4]) / (12*dx**2)
        )
        q_next = 2*q_curr - q_prev + v2dt2 * lap_q
        q_next *= sponge
        q_prev = q_curr.copy()
        q_curr = q_next.copy()

        # Cross-correlate with saved forward snapshot
        if it % stride == 0 and snap_idx >= 0:
            fwd_snap = snapshots[snap_idx]
            snap_idx -= 1
            # ∂J/∂(v²) = -2/v³ * ∂J/∂v; accumulate in v² space for simplicity
            gradient -= fwd_snap * q_curr

    # Convert gradient from v² to v: g_v = g_{v²} * (-2/v³) ... keep sign for descent
    # Simplified: g ∝ gradient (already appropriate for steepest-descent on v²)
    return misfit, gradient


class FWITool(SeismicTool):
    """
    Acoustic FWI using the adjoint-state method (steepest descent).

    Uses shot records from ForwardModelingTool as "observed" data,
    perturbs the true model as an initial guess, then iterates.
    """

    def __init__(
        self,
        n_iterations: int = 5,
        step_size: float = 1e-4,
        smoothing_sigma: float = 3.0,
        perturbation_fraction: float = 0.10,  # how much to bias initial model
    ):
        super().__init__("FWI")
        self.n_iter = n_iterations
        self.alpha = step_size
        self.sigma = smoothing_sigma
        self.perturb = perturbation_fraction

    def run(self, ctx: ToolContext) -> ToolResult:
        vp_true = ctx.velocity_models.get("vp")
        fwd = ctx.forward_data
        if vp_true is None or not fwd:
            return ToolResult(False, self.name,
                              "Requires Vp model and forward data.",
                              error="Missing ctx.velocity_models['vp'] or ctx.forward_data")

        geo = fwd["geometry"]
        dx, dz, dt, nt = geo["dx"], geo["dz"], geo["dt"], geo["nt"]
        nz, nx = geo["nz"], geo["nx"]
        src_z = geo["src_z"]
        shot_xs = np.array(geo["shot_xs"])
        rec_xs = np.array(geo["rec_xs"])
        rec_z = geo["rec_z"]
        rec_pos = np.column_stack([np.full(len(rec_xs), rec_z, dtype=int), rec_xs])

        wavelet = fwd["wavelet"]
        d_obs_all = fwd["shot_records"]   # list [n_shots] of [nt, nr]

        vp_true_crop = vp_true[:nz, :nx].astype(np.float32)

        # ---- Initial model: smooth + perturb ----
        vp_init = gaussian_filter(vp_true_crop, sigma=self.sigma * 5)
        vp_init = vp_init * (1.0 + self.perturb)  # bias high
        vp_cur = vp_init.copy()

        # Clip range to physical bounds
        vmin, vmax = float(vp_true_crop.min()), float(vp_true_crop.max())

        residuals: List[float] = []
        norm_ref = sum(0.5 * np.sum(d**2) for d in d_obs_all) + 1e-30

        self.logger.info(f"Starting FWI: {self.n_iter} iterations, {len(shot_xs)} shots")

        for it in range(self.n_iter):
            total_misfit = 0.0
            total_grad = np.zeros_like(vp_cur)

            for i_shot, (ix_src, d_obs) in enumerate(zip(shot_xs, d_obs_all)):
                m, g = _compute_gradient(
                    vp_cur, dx, dz, dt, nt,
                    wavelet, src_z, ix_src, rec_pos, d_obs
                )
                total_misfit += m
                total_grad += g

            # Normalise gradient
            g_max = np.abs(total_grad).max() + 1e-30
            total_grad /= g_max

            # Step
            vp_cur = vp_cur - self.alpha * total_grad
            vp_cur = np.clip(vp_cur, vmin, vmax)

            normalised = total_misfit / norm_ref
            residuals.append(normalised)
            self.logger.info(f"  Iter {it+1}/{self.n_iter}  misfit={normalised:.6f}")

        ctx.inversion_results = {
            "recovered_vp": vp_cur,
            "initial_vp": vp_init,
            "true_vp": vp_true_crop,
            "residuals": residuals,
        }
        ctx.metrics["fwi"] = {
            "n_iterations": self.n_iter,
            "initial_misfit": residuals[0] if residuals else None,
            "final_misfit": residuals[-1] if residuals else None,
            "misfit_reduction_pct": (
                100 * (residuals[0] - residuals[-1]) / (residuals[0] + 1e-30)
                if len(residuals) >= 2 else 0
            ),
            "velocity_error_rms_ms": float(
                np.sqrt(np.mean((vp_cur - vp_true_crop)**2))
            ),
        }

        fig_path = None
        if _MPL_OK:
            fig_path = self._plot_fwi(ctx.inversion_results, residuals, ctx.outputs_dir)
            ctx.figures.append(fig_path)

        m = ctx.metrics["fwi"]
        summary = (
            f"FWI complete: {self.n_iter} iterations. "
            f"Misfit reduction: {m['misfit_reduction_pct']:.1f}%. "
            f"RMS velocity error: {m['velocity_error_rms_ms']:.1f} m/s."
        )
        if fig_path:
            summary += f" Figure: {fig_path}"
        return ToolResult(True, self.name, summary, data=ctx.inversion_results)

    def _plot_fwi(self, results: dict, residuals: List[float], out_dir: Path) -> str:
        out_dir.mkdir(exist_ok=True)
        true_vp = results["true_vp"]
        init_vp = results["initial_vp"]
        recv_vp = results["recovered_vp"]

        vmin = min(true_vp.min(), recv_vp.min())
        vmax = max(true_vp.max(), recv_vp.max())

        fig, axes = plt.subplots(2, 2, figsize=(14, 8))
        kw = dict(cmap="RdYlBu_r", vmin=vmin, vmax=vmax, aspect="auto")

        axes[0, 0].imshow(true_vp, **kw); axes[0, 0].set_title("True Vp")
        axes[0, 1].imshow(init_vp, **kw); axes[0, 1].set_title("Initial Vp (perturbed)")
        im = axes[1, 0].imshow(recv_vp, **kw); axes[1, 0].set_title("Recovered Vp")
        axes[1, 1].imshow(np.abs(recv_vp - true_vp), cmap="hot", aspect="auto")
        axes[1, 1].set_title("Absolute Error |Recovered - True|")

        plt.colorbar(im, ax=axes[:, :2], shrink=0.6, label="Vp (m/s)")

        # Inset misfit curve
        ax_ins = fig.add_axes([0.72, 0.12, 0.24, 0.25])
        ax_ins.plot(range(1, len(residuals)+1), residuals, "o-", color="steelblue")
        ax_ins.set_title("FWI Misfit", fontsize=9)
        ax_ins.set_xlabel("Iteration", fontsize=8)
        ax_ins.set_ylabel("Norm. misfit", fontsize=8)
        ax_ins.tick_params(labelsize=7)

        plt.suptitle("Full Waveform Inversion Results (Marmousi)", fontsize=13)
        plt.tight_layout(rect=[0, 0, 1, 0.96])

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fig_path = str(out_dir / f"fwi_result_{ts}.png")
        plt.savefig(fig_path, dpi=150, bbox_inches="tight")
        plt.close()
        self.logger.info(f"FWI figure saved: {fig_path}")
        return fig_path
