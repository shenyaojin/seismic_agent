"""
VelocityAnalysisTool: Statistical and petrophysical analysis of velocity models.

Computes:
  - Vp / Vs statistics (min, max, mean, std)
  - Vp/Vs ratio and Poisson's ratio distribution
  - Impedance estimate (requires density assumption)
  - Saves a 2-panel figure (Vp | Vs or Vp/Vs ratio)

Populates ctx.metrics with numerical results.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np

from .base import SeismicTool, ToolContext, ToolResult

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    _MPL_OK = True
except ImportError:
    _MPL_OK = False


def _stats(arr: np.ndarray) -> dict:
    return {
        "min": float(arr.min()),
        "max": float(arr.max()),
        "mean": float(arr.mean()),
        "std": float(arr.std()),
        "p10": float(np.percentile(arr, 10)),
        "p50": float(np.percentile(arr, 50)),
        "p90": float(np.percentile(arr, 90)),
    }


class VelocityAnalysisTool(SeismicTool):
    """
    Statistical analysis of Vp/Vs velocity models.
    Requires ctx.velocity_models to be populated by SEGYLoaderTool.
    """

    def __init__(self, rho_g_cc: float = 2.3):
        """
        Args:
            rho_g_cc: Assumed average density in g/cc for impedance estimation.
        """
        super().__init__("VelocityAnalysis")
        self.rho = rho_g_cc * 1000  # kg/m³

    def run(self, ctx: ToolContext) -> ToolResult:
        models = ctx.velocity_models
        if not models or ("vp" not in models and "vs" not in models):
            return ToolResult(False, self.name, "No velocity models in context.",
                              error="ctx.velocity_models is empty")

        metrics: dict = {}

        vp = models.get("vp")
        vs = models.get("vs")

        if vp is not None:
            metrics["vp"] = _stats(vp)
            # Acoustic impedance (Z = rho * Vp)
            Z = self.rho * vp
            metrics["acoustic_impedance_MRayl"] = {
                "min": float(Z.min() / 1e6),
                "max": float(Z.max() / 1e6),
                "mean": float(Z.mean() / 1e6),
            }

        if vs is not None:
            metrics["vs"] = _stats(vs)

        if vp is not None and vs is not None:
            # Align shapes (take smaller common size)
            nz = min(vp.shape[0], vs.shape[0])
            nx = min(vp.shape[1], vs.shape[1])
            _vp, _vs = vp[:nz, :nx], vs[:nz, :nx]

            ratio = np.where(_vs > 0, _vp / _vs, np.nan)
            poisson = np.where(
                ~np.isnan(ratio),
                (ratio**2 - 2) / (2 * (ratio**2 - 1)),
                np.nan,
            )
            metrics["vp_vs_ratio"] = {
                "mean": float(np.nanmean(ratio)),
                "std": float(np.nanstd(ratio)),
                "p10": float(np.nanpercentile(ratio, 10)),
                "p90": float(np.nanpercentile(ratio, 90)),
            }
            metrics["poisson_ratio"] = {
                "mean": float(np.nanmean(poisson)),
                "p10": float(np.nanpercentile(poisson, 10)),
                "p90": float(np.nanpercentile(poisson, 90)),
            }

        # ---- Figure ----
        fig_path = None
        if _MPL_OK:
            fig_path = self._plot(models, ctx.outputs_dir)
            ctx.figures.append(fig_path)

        ctx.metrics.update(metrics)

        lines = [f"Velocity analysis complete."]
        if "vp" in metrics:
            s = metrics["vp"]
            lines.append(f"Vp: mean={s['mean']:.0f} m/s, range=[{s['min']:.0f}, {s['max']:.0f}]")
        if "vs" in metrics:
            s = metrics["vs"]
            lines.append(f"Vs: mean={s['mean']:.0f} m/s, range=[{s['min']:.0f}, {s['max']:.0f}]")
        if "vp_vs_ratio" in metrics:
            r = metrics["vp_vs_ratio"]
            p = metrics["poisson_ratio"]
            lines.append(f"Vp/Vs: mean={r['mean']:.2f} | Poisson σ: mean={p['mean']:.3f}")
        if fig_path:
            lines.append(f"Figure: {fig_path}")

        return ToolResult(True, self.name, "\n".join(lines), data=metrics)

    def _plot(self, models: dict, out_dir: Path) -> str:
        out_dir.mkdir(exist_ok=True)
        vp = models.get("vp")
        vs = models.get("vs")

        n_panels = sum(x is not None for x in [vp, vs])
        if n_panels == 0:
            return ""

        # If both available, also add Vp/Vs ratio panel
        if vp is not None and vs is not None:
            nz = min(vp.shape[0], vs.shape[0])
            nx = min(vp.shape[1], vs.shape[1])
            ratio = np.where(vs[:nz, :nx] > 0, vp[:nz, :nx] / vs[:nz, :nx], np.nan)
            panels = [("Vp (m/s)", vp, "RdYlBu_r"),
                      ("Vs (m/s)", vs, "RdYlBu_r"),
                      ("Vp/Vs ratio", ratio, "viridis")]
        elif vp is not None:
            panels = [("Vp (m/s)", vp, "RdYlBu_r")]
        else:
            panels = [("Vs (m/s)", vs, "RdYlBu_r")]

        fig, axes = plt.subplots(1, len(panels), figsize=(6 * len(panels), 5))
        if len(panels) == 1:
            axes = [axes]

        dx = models.get("dx", 1.25)
        dz = models.get("dz", 1.25)

        for ax, (title, data, cmap) in zip(axes, panels):
            nz_, nx_ = data.shape
            extent = [0, nx_ * dx / 1000, nz_ * dz / 1000, 0]  # km
            im = ax.imshow(data, cmap=cmap, aspect="auto", extent=extent)
            ax.set_title(title, fontsize=12)
            ax.set_xlabel("Distance (km)")
            ax.set_ylabel("Depth (km)")
            plt.colorbar(im, ax=ax, shrink=0.8)

        plt.suptitle("Marmousi Velocity Model Analysis", fontsize=14, fontweight="bold")
        plt.tight_layout()

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fig_path = str(out_dir / f"velocity_model_{ts}.png")
        plt.savefig(fig_path, dpi=150, bbox_inches="tight")
        plt.close()
        self.logger.info(f"Velocity figure saved: {fig_path}")
        return fig_path
