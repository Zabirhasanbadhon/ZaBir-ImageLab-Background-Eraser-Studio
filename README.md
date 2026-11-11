<div align="center">

# ✨ ZaBir - ImageLab Background Eraser Studio

### AI-Powered Background Remover & Image Editor

**Remove backgrounds. Edit images. Create something amazing.**

A powerful, modern, all-in-one desktop image editing application built with  
**Python · Tkinter · Pillow · rembg · ONNX Runtime**

<br>

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-555555?style=for-the-badge)
![AI](https://img.shields.io/badge/AI-Powered-8A2BE2?style=for-the-badge)
![Processing](https://img.shields.io/badge/Processing-100%25%20Local-00A86B?style=for-the-badge)

<br>

**🔒 Private & Local Processing** · **⚡ AI Background Removal** · **🎨 Professional Editing**

</div>

---

## 📸 About the Project

**ZaBir ImageLab** is a feature-rich desktop application designed to make background removal and everyday image editing simple, fast, and accessible.

Powered by AI through `rembg` and `ONNX Runtime`, it removes image backgrounds directly on your computer — without uploading your images to external servers.

Whether you're creating product images, editing portraits, preparing social media graphics, or simply enhancing your photos, ImageLab brings essential editing tools together in one application.

### 🌟 Why ZaBir ImageLab?

- 🤖 AI-powered background removal with five selectable models.
- 🔒 Complete local processing — your images stay on your device.
- 🎨 Powerful image editing and adjustment tools.
- 🖼️ Flexible background replacement options.
- 📐 Ready-to-use social media image presets.
- ↩️ Undo and redo with up to 30 history states.
- 🔍 Smooth zoom, pan, and image comparison.
- 💾 Export images in PNG, JPEG, and WEBP formats.

---

## 🚀 Features

### 🤖 1. AI Background Removal

Remove image backgrounds automatically using locally running AI models.

**Five selectable AI models:**

| AI Model | Best For | Description |
|:---|:---|:---|
| `u2net` ⭐ | General use | Best overall quality (default) |
| `u2netp` ⚡ | Speed | Lightweight and fastest inference |
| `isnet-general-use` | General subjects | Versatile background removal |
| `silueta` | Lightweight use | Small model footprint |
| `u2net_human_seg` | Portraits | Specialized human segmentation |

**Highlights:**
- Fully offline processing after model download.
- Lazy model initialization.
- Background inference runs in a separate thread.
- Model selection through application settings.
- No cloud uploads or external image processing.

> 💡 **Note:** An internet connection is required the first time a selected AI model is downloaded.

---

### 🎨 2. Powerful Image Editing

Everything you need for everyday image editing.

**Transform Tools**

- Rotate 90° left.
- Rotate 90° right.
- Rotate 180°.
- Flip horizontally.
- Flip vertically.

**Crop Editor**

A flexible crop interface featuring:

- Drag-to-draw crop selection.
- Free-form cropping.
- Aspect-ratio presets.
- Rule-of-thirds composition guides.
- Resizable corner handles.

Supported aspect ratios:

`Free` · `1:1` · `4:5` · `3:2` · `4:3` · `16:9` · `9:16`

**Resize Tools**

- Custom width and height.
- Optional aspect-ratio locking.
- High-quality Lanczos resampling.

---

### 📱 3. Social Media Presets

Prepare your images for popular social media platforms with convenient preset dimensions.

| Platform | Preset |
|:---|:---|
| 📸 Instagram | Square |
| 📸 Instagram | Portrait |
| 📱 Instagram | Story |
| ▶️ YouTube | Thumbnail |
| 📘 Facebook | Cover |
| 💼 LinkedIn | Post |

**Image fitting modes:**

- `Cover` — Fill the entire canvas.
- `Contain` — Fit the entire image without cropping.
- `Stretch` — Stretch to the target dimensions.

---

### 🪄 4. Image Adjustments

Fine-tune your images with four essential adjustment controls.

| Adjustment | Function |
|:---|:---|
| ☀️ Brightness | Control image brightness |
| ◐ Contrast | Adjust light and dark differences |
| 🌈 Saturation | Enhance or reduce color intensity |
| ✨ Sharpness | Improve image detail and clarity |

**Smart Preview System**

- Fast, low-resolution live previews.
- Full-resolution rendering when applying changes.
- Reduced unnecessary processing during slider adjustments.

---

### 🖼️ 5. Background Replacement

Replace removed backgrounds with your preferred design.

**Available background options:**

| Option | Description |
|:---|:---|
| Transparent | Keep the image background transparent |
| Solid Color | Choose a custom background color |
| Background Image | Use another image as the background |

**Color Selection**

- Built-in color palette.
- HEX color input.
- Native color picker.

**Background Image Controls**

- Cover
- Contain
- Stretch
- Crop

Fine-tune background positioning using horizontal (X) and vertical (Y) position sliders.

---

### 🔍 6. Image Preview & Navigation

Enjoy a flexible workspace with convenient image viewing controls.

- **Before / After Toggle** — Compare original and edited images.
- **Split Comparison** — Drag a divider to compare both versions.
- **Zoom to Fit** — Automatically fit images inside the workspace.
- **100% Zoom** — View images at their original size.
- **Zoom In / Out** — Adjust magnification.
- **Pan** — Navigate using middle or right mouse-button dragging.
- **Mouse Wheel** — Zoom in and out smoothly.

**Transparency Preview**

A checkerboard pattern visually represents transparent areas.

> The checkerboard is only a preview background and is never included in exported images.

---

### ↩️ 7. Undo & Redo History

Experiment freely without worrying about losing your previous edits.

- Undo previous operations.
- Redo undone operations.
- Snapshot-based editing history.
- Bounded history with a maximum of **30 states**.

---

### 💾 8. Export & Save

Export your finished images in three popular formats.

| Format | Transparency | Description |
|:---|:---:|:---|
| PNG | ✅ Yes | Lossless image export |
| JPEG | ❌ No | Flattened image with adjustable quality |
| WEBP | ✅ Yes | Modern image format with alpha support |

**Additional Export Options**

- Custom output dimensions.
- Adjustable JPEG quality.
- Custom JPEG background color.
- EXIF metadata preservation.
- Configurable export directory.

---

### 🖥️ 9. Modern Desktop Interface

Designed for a comfortable image editing experience.

- Modern dark theme.
- Configurable sidebar width.
- Recent files menu.
- Drag-and-drop image opening.
- Keyboard shortcuts.
- Responsive window layout.
- Background task processing.
- Optional `tkinterdnd2` integration.

---

## 🛠️ Built With

The application uses the following technologies:

| Technology | Purpose |
|:---|:---|
| 🐍 Python | Core programming language |
| 🪟 Tkinter | Desktop graphical interface |
| 🖼️ Pillow | Image processing and manipulation |
| 🤖 rembg | AI-powered background removal |
| ⚡ ONNX Runtime | AI model inference engine |
| 🔢 NumPy | Numerical image operations |
| 🌐 Requests | HTTP and resource handling |
| 📂 tkinterdnd2 | Optional drag-and-drop support |

---

## 📋 Requirements

Before installing, make sure you have:

- Python **3.11 recommended**.
- Python 3.9+ may work, depending on dependency compatibility.
- Windows, macOS, or Linux.
- Tkinter support.
- Approximately 170 MB of disk space for the default AI model.
- Internet access for the initial AI model download.

---

## ⌨️ Keyboard Shortcuts

Work faster using built-in keyboard shortcuts.

| Shortcut | Action |
|:---|:---|
| `Ctrl + O` | Open image |
| `Ctrl + S` | Export image |
| `Ctrl + Z` | Undo |
| `Ctrl + Y` | Redo |
| `Ctrl + Shift + Z` | Redo |
| `Ctrl + 0` | Fit image to window |
| `Ctrl + +` | Zoom in |
| `Ctrl + -` | Zoom out |
| `Ctrl + B` | Toggle tools sidebar |
| `Esc` | Cancel crop |

---

## 🖱️ Mouse Controls

| Mouse Input | Action |
|:---|:---|
| Mouse Wheel | Zoom in / out |
| Middle Mouse Drag | Pan image |
| Right Mouse Drag | Pan image |
| Left Drag (Split Mode) | Move comparison divider |
| Left Drag (Crop Mode) | Draw crop selection |
| Crop Corner Drag | Resize crop selection |

---

## ⚙️ Configuration & Logging

ImageLab automatically manages its application configuration and logs.

### `config.json`

Stored alongside the Python script.

It stores:

- Window dimensions.
- Sidebar width.
- Last export directory.
- Preferred output format.
- Export quality.
- Selected AI model.
- Checkerboard size.
- Recent files.

Invalid configuration values safely fall back to default settings.

### `imagelab.log`

Application logs are stored in:

```text
imagelab.log
```

This file can help diagnose errors and troubleshoot unexpected behavior.

---

## 📁 Project Architecture

ZaBir ImageLab AI is designed as a **single-file desktop application**, organized internally into logical components.

| Component | Responsibility |
|:---|:---|
| Logging & Configuration | `setup_logging`, `load_config`, `save_config` |
| Image Helpers | Image loading, resizing, compositing, adjustments and exporting |
| History Management | `Snapshot`, `HistoryManager` |
| AI Engine | `BackgroundRemover`, `BackgroundRemovalError` |
| Task Management | `TaskRunner` |
| UI Components | `RoundButton`, `Swatch`, `ScrollableFrame` |
| Main Application | `App` |
| Testing | `run_selftests` |

---

## 🔧 Troubleshooting

| Problem | Possible Solution |
|:---|:---|
| AI engine not installed | Run `pip install "rembg[cpu]" onnxruntime` inside the application's virtual environment. |
| AI model fails to download | Check your internet connection, firewall, or proxy settings. |
| AI model cannot detect the background | Try `isnet-general-use` or `u2net_human_seg`. |
| Drag-and-drop does not work | Install `tkinterdnd2` and restart the application. |
| Export permission denied | Select a writable output directory. |
| Tkinter missing on Linux | Install `python3-tk` using your package manager. |
| Preview quality looks reduced | Apply adjustments to render the image at full resolution. |

---

## ⚠️ Notes & Limitations

- All image processing takes place locally.
- No images are uploaded to cloud servers.
- An internet connection is required for the initial AI model download.
- AI inference cannot be interrupted once started; cancelling discards the result when processing finishes.
- JPEG does not support transparency, so transparent areas are flattened onto a selected background color.
- AI inference uses the ONNX Runtime CPU build.
- GPU acceleration is not currently configured.
- Preview adjustments use reduced-resolution images for performance.
- No license file is currently included. Add an appropriate license before distributing the project.

---

## 👨‍💻 Developer

<div align="center">

### ZaBir HaSan

**Designed & Developed by ZaBir Hasan**

*Turning creative ideas into powerful desktop applications.*

<br>

![Made with Python](https://img.shields.io/badge/Made%20with-Python-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Powered by AI](https://img.shields.io/badge/Powered%20by-AI-8A2BE2?style=for-the-badge)

<br>

**⭐ If you like this project, consider giving it a star!**

</div>

---

<div align="center">

### ✨ ZaBir ImageLab
**Your Images. Your Creativity. Your Control.**

</div>
