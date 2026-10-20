# Academic Image to Editable PPT

将论文图、流程图、截图等图片转换为可编辑 PowerPoint。

文字、模块框和连线原生编辑；图标、插画和图表采用无损抠取或 API 生成，作为独立高清素材使用。

## 效果对比

左侧为原图，右侧为 PowerPoint 导出效果。

![原图与可编辑 PPT 对比](docs/images/live-comparison.png)

[下载示例 PPTX](examples/live/EEGAgent-Live-editable.pptx?raw=true) · [查看预览](examples/live/preview.png)

## 安装

```powershell
git clone https://github.com/khalil-wu/academic-image-to-editable-ppt.git "$env:USERPROFILE/.codex/skills/image-to-editable-ppt"
python -m pip install -r "$env:USERPROFILE/.codex/skills/image-to-editable-ppt/requirements.txt"
```

## 使用

在 Codex 中附上图片并调用：

```text
$image-to-editable-ppt 把这张图转成PPTX，保留布局与清晰度，素材只抠取或用API生成，不描摹。
```

复现仓库示例：

```powershell
python examples/live/rebuild.py
```

[详细工作流程](SKILL.md) · [示例工程](examples/live) · [Apache-2.0](LICENSE)
