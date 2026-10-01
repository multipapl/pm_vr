"""Plot exact atlas files produced by blender_real_pbr_review.py."""
import json
from pathlib import Path
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image

root = Path(sys.argv[1])
report = json.loads((root / 'pbr_review.json').read_text())
before, after = [np.asarray(Image.open(item['atlas']).convert('RGB')) / 255
                 for item in report['products']]
fig, axes = plt.subplots(1, 3, figsize=(13, 4.8), layout='constrained')
fig.suptitle(f"{report['unit']} — real UniPlace test copy, {report['resolution']}px, "
             f"{report['samples']} samples, {report.get('denoise', 'OFF')} denoise")
for axis, title, pixels in zip(axes[:2], ('Combined (v2 mode)', 'PBR Diffuse Only'), (before, after)):
    axis.imshow(pixels)
    axis.set_title(title)
    axis.axis('off')
delta = np.mean(np.abs(before - after), axis=-1)
artist = axes[2].imshow(delta, cmap='magma', vmin=0, vmax=max(.1, float(np.quantile(delta, .99))))
axes[2].set_title('Absolute PNG RGB difference')
axes[2].axis('off')
fig.colorbar(artist, ax=axes[2], shrink=.7, label='sRGB file values (0–1)')
fig.text(.5, .005, f"Mean linear bake RGB change: {report['mean_linear_rgb_delta']:.6f}. "
         'Geometry, UVs, authored USD and original PBR channels unchanged.', ha='center', fontsize=10)
fig.savefig(root / 'FaucetMetal_comparison.png', dpi=160)
print(root / 'FaucetMetal_comparison.png')
