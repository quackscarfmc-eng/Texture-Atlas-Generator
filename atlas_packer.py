#!/usr/bin/env python3
"""
Texture Atlas Packer
--------------------
- Drag-and-drop (or "Add Files") any valid image format
- Add textures to an EXISTING atlas (Load Atlas button)
- MaxRects bin packing (BSSF heuristic) with 1-px padding
- Power-of-two (optional) or arbitrary atlas sizes
- Exports atlas PNG + JSON metadata with UV rects
- Highly optimised for legacy hardware (AntiX OS)
"""

import os
import sys
import json
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    sys.stderr.write("Pillow is required: sudo apt install python3-pil\n")
    sys.exit(1)

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    import tkinterdnd2 as dnd
    HAS_DND = True
except Exception:
    HAS_DND = False


# ---------------------------------------------------------------------------
# MaxRects bin packer (BSSF: Best Short Side Fit)
# ---------------------------------------------------------------------------
class MaxRectsBin:
    __slots__ = ("width", "height", "free", "used")

    def __init__(self, width, height, used_rects=None):
        self.width = width
        self.height = height
        self.free = [(0, 0, width, height)]
        self.used = []
        # Pre-populate occupied space if adding to an existing atlas
        if used_rects:
            for r in used_rects:
                self._place(r)

    def insert(self, w, h):
        best = None
        best_rect = None
        for (rx, ry, rw, rh) in self.free:
            if w <= rw and h <= rh:
                ss = min(rw - w, rh - h)
                ls = max(rw - w, rh - h)
                score = (ss, ls)
                if best is None or score < best:
                    best = score
                    best_rect = (rx, ry, w, h)
        if best_rect is None:
            return None
        self._place(best_rect)
        return best_rect

    def _place(self, rect):
        px, py, pw, ph = rect
        new_free = []
        for (rx, ry, rw, rh) in self.free:
            # no overlap
            if px + pw <= rx or px >= rx + rw or py + ph <= ry or py >= ry + rh:
                new_free.append((rx, ry, rw, rh))
                continue
            # split into up to 4 sub-rects
            if px > rx:
                new_free.append((rx, ry, px - rx, rh))
            if px + pw < rx + rw:
                new_free.append((px + pw, ry, rx + rw - (px + pw), rh))
            if py > ry:
                new_free.append((rx, ry, rw, py - ry))
            if py + ph < ry + rh:
                new_free.append((rx, py + ph, rw, ry + rh - (py + ph)))
        # prune contained rects
        pruned = []
        n = len(new_free)
        for i in range(n):
            a = new_free[i]
            ax, ay, aw, ah = a
            contained = False
            for j in range(n):
                if i == j:
                    continue
                b = new_free[j]
                if (ax >= b[0] and ay >= b[1] and
                        ax + aw <= b[0] + b[2] and ay + ah <= b[1] + b[3]):
                    contained = True
                    break
            if not contained:
                pruned.append(a)
        self.free = pruned
        self.used.append(rect)


# ---------------------------------------------------------------------------
# Atlas builder
# ---------------------------------------------------------------------------
PADDING = 1  # px between sprites, avoids UV bleeding
MAX_ATLAS = 8192  # cap for old GPU friendliness


def pack_atlas(paths, power_of_two=True, bg=(0, 0, 0, 0), existing_atlas=None):
    """Returns (atlas_image, entries) or raises."""
    images = []
    for p in paths:
        try:
            im = Image.open(p).convert("RGBA")
            im.load()  # force load now so file handle can close
        except Exception as e:
            raise RuntimeError(f"Failed to open {p}: {e}")
        images.append((Path(p).name, im))

    # Sort by max side descending — standard improvement
    images.sort(key=lambda kv: max(kv[1].size), reverse=True)

    # If we have an existing atlas, try to pack new images into the remaining space
    if existing_atlas:
        atlas_w = existing_atlas['width']
        atlas_h = existing_atlas['height']
        
        # Pad existing sprite bounds by 1px to simulate original padding
        used_rects = [(s['x'], s['y'], s['width'] + PADDING, s['height'] + PADDING) for s in existing_atlas['sprites']]
        
        bin_ = MaxRectsBin(atlas_w, atlas_h, used_rects)
        placed = {}
        ok = True
        
        for name, im in images:
            w, h = im.size
            rect = bin_.insert(w + PADDING, h + PADDING)
            if rect is None:
                ok = False
                break
            placed[name] = (rect[0], rect[1], w, h)
            
        if ok:
            # They fit! Paste them onto the existing atlas canvas
            atlas_img = existing_atlas['image_obj'].copy()
            entries = existing_atlas['sprites'].copy()
            
            for name, im in images:
                px, py, w, h = placed[name]
                atlas_img.paste(im, (px, py))
                entries.append({
                    "name": name,
                    "x": px, "y": py, "width": w, "height": h,
                    "u0": px / atlas_w, "v0": py / atlas_h,
                    "u1": (px + w) / atlas_w, "v1": (py + h) / atlas_h,
                })
            
            for _, im in images:
                im.close()
            entries.sort(key=lambda e: e["name"])
            return atlas_img, entries
        else:
            # They didn't fit in the old size. Crop old sprites and repack everything from scratch.
            for s in existing_atlas['sprites']:
                x, y, w, h = s['x'], s['y'], s['width'], s['height']
                cropped = existing_atlas['image_obj'].crop((x, y, x + w, y + h))
                images.append((s['name'], cropped))
            images.sort(key=lambda kv: max(kv[1].size), reverse=True)

    # Standard packing routine (used for fresh atlases or when growing an existing one)
    total_area = sum(im.size[0] * im.size[1] for _, im in images)
    start = 64
    while start * start < total_area * 1.25 and start < MAX_ATLAS:
        start *= 2

    size = start
    while size <= MAX_ATLAS:
        bin_ = MaxRectsBin(size, size)
        placed = {}
        ok = True
        for name, im in images:
            w, h = im.size
            rect = bin_.insert(w + PADDING, h + PADDING)
            if rect is None:
                ok = False
                break
            placed[name] = (rect[0], rect[1], w, h)
        if ok:
            return _render(size, images, placed, bg)
        size *= 2
        
    raise RuntimeError("Atlas exceeds maximum size cap (8192px).")


def _render(size, images, placed, bg):
    atlas = Image.new("RGBA", (size, size), bg)
    entries = []
    for name, im in images:
        px, py, w, h = placed[name]
        atlas.paste(im, (px, py))
        entries.append({
            "name": name,
            "x": px,
            "y": py,
            "width": w,
            "height": h,
            "u0": px / size,
            "v0": py / size,
            "u1": (px + w) / size,
            "v1": (py + h) / size,
        })
    for _, im in images:
        im.close()
    entries.sort(key=lambda e: e["name"])
    return atlas, entries


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------
class AtlasApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Texture Atlas Packer")
        self.root.geometry("720x600")
        self.root.minsize(560, 460)

        self.paths = []  # list of str
        self.existing_atlas = None

        # --- Drop zone ---------------------------------------------------
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

        self.drop = tk.Label(
            root,
            text=("Drag and drop NEW image files here\n"
                  "or click \"Add Files\""),
            relief="groove",
            bd=2,
            bg="#2b2b2b",
            fg="#dddddd",
            font=("TkDefaultFont", 12),
            pady=40,
        )
        self.drop.pack(fill="both", expand=True, padx=12, pady=(12, 6))

        if HAS_DND:
            self.drop.drop_target_register(dnd.DND_FILES)
            self.drop.dnd_bind("<<Drop>>", self.on_drop)

        self.drop.bind("<Button-1>", lambda e: self.add_files())

        # --- List --------------------------------------------------------
        self.listbox = tk.Listbox(root, height=8, selectmode="extended",
                                  bg="#1e1e1e", fg="#dddddd",
                                  selectbackground="#3a6ea5",
                                  highlightthickness=0, bd=0)
        self.listbox.pack(fill="both", expand=True, padx=12, pady=6)

        # --- Controls ----------------------------------------------------
        ctrl = ttk.Frame(root)
        ctrl.pack(fill="x", padx=12, pady=(0, 6))

        ttk.Button(ctrl, text="Add Files", command=self.add_files).pack(side="left")
        ttk.Button(ctrl, text="Remove Selected",
                   command=self.remove_selected).pack(side="left", padx=4)
        ttk.Button(ctrl, text="Clear", command=self.clear).pack(side="left")
        
        ttk.Button(ctrl, text="Load Existing Atlas",
                   command=self.load_existing).pack(side="left", padx=8)

        self.pot_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(ctrl, text="Power-of-two size",
                        variable=self.pot_var).pack(side="left", padx=8)

        ttk.Button(ctrl, text="Export Atlas",
                   command=self.export).pack(side="right")

        # status
        self.status = ttk.Label(root, text="Ready.", anchor="w")
        self.status.pack(fill="x", padx=12, pady=(0, 6))

    def _is_valid_image(self, filepath):
        """Checks if a file can be opened as an image by Pillow."""
        try:
            with Image.open(filepath) as im:
                im.verify()
            return True
        except Exception:
            return False

    # -- DnD -------------------------------------------------------------
    def on_drop(self, event):
        files = self.root.tk.splitlist(event.data)
        added = 0
        for f in files:
            # TkinterDnD may wrap paths with spaces in curly braces
            f = f.strip("{}")
            if os.path.isfile(f) and f not in self.paths:
                # Attempt to validate any file dropped as an image
                if self._is_valid_image(f):
                    self.paths.append(f)
                    self.listbox.insert("end", os.path.basename(f))
                    added += 1
        self._update_status(added)

    # -- Buttons ---------------------------------------------------------
    def add_files(self):
        # Separated file extensions fix the blank screen issue on older Linux Tk versions
        files = filedialog.askopenfilenames(
            title="Select images",
            filetypes=[
                ("Image files", "*.png *.jpg *.jpeg *.bmp *.gif *.tga *.tif *.tiff *.webp *.ppm *.pgm *.pbm *.ico"),
                ("PNG", "*.png"),
                ("JPEG", "*.jpg *.jpeg"),
                ("WebP", "*.webp"),
                ("All files", "*.*")
            ])
        added = 0
        for f in files:
            if f in self.paths:
                continue
            # Validate image even if selected from "All files"
            if self._is_valid_image(f):
                self.paths.append(f)
                self.listbox.insert("end", os.path.basename(f))
                added += 1
        self._update_status(added)

    def remove_selected(self):
        for idx in reversed(self.listbox.curselection()):
            self.listbox.delete(idx)
            del self.paths[idx]
        self.status.config(text=f"{len(self.paths)} file(s) queued.")

    def clear(self):
        self.paths.clear()
        self.listbox.delete(0, "end")
        self.status.config(text="Cleared.")

    def load_existing(self):
        json_path = filedialog.askopenfilename(
            title="Select existing atlas metadata",
            filetypes=[("JSON metadata", "*.json"), ("All files", "*.*")])
        if not json_path:
            return
            
        try:
            with open(json_path, "r") as f:
                meta = json.load(f)
            
            img_name = meta.get("image")
            img_path = str(Path(json_path).parent / img_name)
            
            img_obj = Image.open(img_path).convert("RGBA")
            img_obj.load()
            
            self.existing_atlas = {
                "image_obj": img_obj,
                "width": meta["width"],
                "height": meta["height"],
                "sprites": meta["sprites"]
            }
            self.status.config(text=f"Loaded existing atlas: {img_path}. Drop new textures to append.")
        except Exception as e:
            messagebox.showerror("Load Error", f"Failed to load atlas:\n{e}")

    def _update_status(self, added):
        self.status.config(text=f"Added {added}. Total: {len(self.paths)} file(s) queued.")

    # -- Export ----------------------------------------------------------
    def export(self):
        if not self.paths:
            messagebox.showinfo("No images",
                                "Add or drop some image files first.")
            return

        out_path = filedialog.asksaveasfilename(
            title="Export atlas",
            defaultextension=".png",
            filetypes=[("PNG", "*.png")])
        if not out_path:
            return

        self.status.config(text="Packing… please wait.")
        self.root.update_idletasks()

        try:
            atlas, entries = pack_atlas(
                self.paths,
                power_of_two=self.pot_var.get(),
                existing_atlas=self.existing_atlas)
        except Exception as e:
            messagebox.showerror("Error", str(e))
            self.status.config(text="Failed.")
            return

        try:
            atlas.save(out_path, "PNG", optimize=True)
        except Exception as e:
            messagebox.showerror("Save error", str(e))
            self.status.config(text="Failed to save.")
            return

        meta_path = Path(out_path).with_suffix(".json")
        try:
            with open(meta_path, "w") as f:
                json.dump({
                    "image": Path(out_path).name,
                    "width": atlas.width,
                    "height": atlas.height,
                    "sprites": entries,
                }, f, indent=2)
        except Exception as e:
            messagebox.showwarning("Metadata",
                                   f"Atlas saved, but metadata write failed:\n{e}")

        atlas.close()
        self.status.config(
            text=f"Exported {atlas.width}x{atlas.height} atlas → {out_path}")


def main():
    TkClass = dnd.Tk if HAS_DND else tk.Tk
    root = TkClass()
    AtlasApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
