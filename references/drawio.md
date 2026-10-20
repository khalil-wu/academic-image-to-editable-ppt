# draw.io：从图片或已有 PPTX 生成可编辑图

## 两条入口

用户要求 `.drawio` 时输出 mxGraph 文档，而不是把整页 PNG 包在 draw.io 中。画布比例、页序、文字、图像来源和用户已手调的版本均沿用源稿。

**图片 → 场景与素材 → draw.io**

按主流程识别文字和关系，准备 `scene.json`、`assets.json` 与独立高清图片，然后运行：

```powershell
python scripts/build_drawio.py scene.json figure.drawio --assets assets.json
```

构建器复用原生布局、素材插入和分组工具，在临时目录生成结构化 PPTX 后转换；不会渲染整页截图再导入。已有场景时直接复用，避免重复识别和重绘。

**已有 PPTX → draw.io**

```powershell
python scripts/pptx_to_drawio.py figure.pptx figure.drawio
python scripts/pptx_to_drawio.py figure.pptx selected.drawio --pages 1 3 --page-names "系统总览" "在线分析"
```

默认保留全部页；`--pages` 使用源 PPT 的一基页码，`--page-names` 与选中页顺序对应。默认尺寸换算为每 PowerPoint 点 `4/3` 个 draw.io 像素，可用 `--scale` 调整。转换器旁写 `.conversion.json`，记录来源哈希、对象数、素材、字体、边及推断连接。

## 结构与素材

| 源组件 | draw.io 组件与编辑能力 |
| --- | --- |
| 文字、矩形、圆角框、椭圆 | 原生 `mxCell`；可改字、颜色、位置与尺寸 |
| 语义模块分组 | group 父节点与相对位置子节点；保留分组层级 |
| 直线、折线、开放曲线 | 原生 edge；端点绑定、路径和箭头分别核对 |
| PPT 原生表格 | 分组的原生矩形单元格，各格文字可改；不是 draw.io table 插件，不能据此承诺插件式增删行列 |
| 位图插画、图标、照片、图表 | 独立内嵌图片，保留原始字节；PPT 有裁剪时按源像素裁剪并保存 PNG，不降采样 |
| 真正存在的源矢量 | 直接转换原路径，可作为独立 SVG 素材保留清晰度；SVG 素材不等于可在编辑器内逐节点修改 |

`--asset-group-prefixes` 默认识别 `ICON_`、`CHART_` 分组，把无文字、无位图的源矢量组转为 SVG。前缀只是转换选择规则，不能作为原创或矢量来源证明。其他已有自定义原生形状保留为 stencil；需要时在源文件修改几何，而不是以栅格化代替。

**禁止描摹仍适用于两个入口的所有位图**，包括 API 生成图。只抠取原图或实际调用用户指定的模型生成独立素材；不要为了更多“原生对象”把图片追踪成路径。

## 分组、文字与字体

PPT 分组的子坐标不等于页面坐标。读取 `off/ext/chOff/chExt`，把父级变换逐层合成。例如单轴换算：

```text
groupScaleX = ext.cx / chExt.cx
pageX = parentScaleX × (off.x + (childX − chOff.x) × groupScaleX) + parentOffsetX
```

Y 轴同理。draw.io 子元素几何使用所属分组的相对坐标；不能只减去组的左上角而忽略缩放。用含嵌套组的实际文件检查位置与移动行为。

模块采用 draw.io 命名样式 `group;collapsible=0;recursiveResize=1;connectable=0;`，不要设置 `container=1`。编辑器的 `isPropagateSelectionCell` 不向 container 父节点传播首次选择，可能只选中子文本；有父子关系不等于首次点击就能选中并移动整个模块。

文字保留为原生富文本。除 HTML 段落 `text-align` 外，`mxCell` style 也要明确 `align=left/center/right`，同时核对 `verticalAlign` 和边距；只写 HTML 对齐可能被编辑器默认值覆盖。

目标机器已安装所需字体时可直接使用字体名。需要文档自带字体时，仅嵌入用户有权使用、且许可允许嵌入/分发的文件：

```powershell
python scripts/pptx_to_drawio.py figure.pptx figure.drawio --embed-font fonts/Body-Regular.ttf --embed-font fonts/Body-Bold.ttf
python scripts/build_drawio.py scene.json figure.drawio --assets assets.json --embed-font fonts/Body-Regular.ttf
```

转换器使用字体文件的真实 family、weight、italic 信息区分常规与粗体，记录许可文本和哈希；不会自动判定许可是否允许发布。`mxGraphModel` 的 `extFonts` 格式为：

```text
Font Name^URL|Second Face^URL
```

可用经许可的字体 data URL，不使用 JSON 或分号拼接代替该格式。随公开示例保留许可证；没有许可时沿用已安装字体或按用户接受的替代字体排版，不能把字转轮廓。字体名写进 XML 不能证明实际加载，必须在真实编辑器检查中英文、粗体、换行与字框范围。

## 边、端口与曲线

优先保留 PPT 的 `stCxn/endCxn` 对应关系，写入 edge 的 `source/target`，并用 `exitX/exitY`、`entryX/entryY` 指定端口。默认端口索引 `0/1/2/3` 对应上/左/下/右；转换后核对实际箭头方向和贴边位置。`sourcePoint/targetPoint` 只是坐标，不代表绑定成功；报告中的 `bound_edges` 指两端均有对象引用。

确需补齐源稿缺失的连接引用时，可运行：

```powershell
python scripts/pptx_to_drawio.py figure.pptx figure.drawio --bind-nearby
python scripts/build_drawio.py scene.json figure.drawio --assets assets.json --bind-nearby
```

该选项只对带箭头、未绑定的端点，在距离小于 18 个 draw.io 像素的 `MODULE_` 分组边界附近推断透明子端口。检查报告中的 `inferred_connections`，逐条确认语义；不能把“足够近”当作正确数据流。移动相连模块后确认端点随动。装饰分隔线可以不绑定，不必为凑满绑定率制造关系。

已有二次/三次 Bézier 边的每段曲线分成 8 个采样区间，再作为 draw.io curved edge 的可编辑路径点。它是近似转换；重点比较闭环、返回线、交叉处和箭头，不称完全保留了原始曲率。`curve_edges_approximated` 列出涉及的边，需要时在真实编辑器中微调。

## 实际编辑器验收

所有构建完成后集中验收，并保留最终文件、预览和 `.conversion.json`：

1. 在真实 draw.io/diagrams.net 编辑器打开全部页面，核对文本数量、页序、字体、素材清晰度、分组、曲线和遮挡。
2. 在副本中双击改一处标题和一个表格单元格；选择并替换一张独立图片。恢复原内容后再交付。
3. 移动一个含子元素的模块，检查分组相对位置和已绑定边的端点；嵌套组也要覆盖。引用存在只是结构证据，实际随动才是行为验证。
4. 用编辑器保存到文件，关闭并重新打开该保存稿，核对内容、字体、素材和连接仍在。仅解析 XML 或直接浏览生成稿不算保存回读通过。

用已有工具统一核对结构和实际编辑器保存稿：

```powershell
python scripts/audit_drawio.py figure.drawio --compare editor-roundtrip.drawio --output audit.json
```

审计覆盖对象 IDs、文字、图片、字体与引用，配合实际改字和模块移动检查即可；不用额外搭建验收系统。

未做完的项目注明待验收，不用结构计数替代真实操作结果。原始用户文件不承担破坏性编辑测试。

## EEGAgent 实践记录（2026-10-20）

当前案例目录为工作区 `Final/output/20261020_Drawio实践/`；这是实测记录，不是其他用户任务的固定目录。

| 文件/页面 | 结构覆盖 |
| --- | --- |
| `EFAgent系统.drawio` 第 1 页：系统总览 | 121 个文本框 |
| 第 2 页：设备接入 | 17 个含文本对象：8 个文本框 + 9 个原生表格单元格 |
| 第 3 页：在线分析 | 24 个文本框 |
| `EEGAgent-Live-scene.drawio`：由场景和素材构建 | 90 个 vertices、24 个文本框、7 个 groups、24 张独立图片、16 条 edges，其中 7 条两端绑定 |

已在真实编辑器中把设备页单元格 `8` 改为 `9`、把标题改为“编辑验证”，并恢复原文，确认相应文字对象可编辑。

分组使用上述命名样式修正后，实际选中整个模块的选框约为 `215 × 225` 屏幕像素；按 Shift + Right，内部图片随模块移动约 `9.9` 屏幕像素，已绑定紫色连线的 path 随端点改变。中央处理模块也通过移动与连线随动测试。

已完成编辑器 XML 导出回读：`文件 → 导出为 → XML`，选所有页面、非压缩，导出后在“另存为”选择“下载”；等按钮由“保存”变为“确定”再点击，取得 `editor-roundtrip.drawio`。回读导出 XML，三页的 IDs、文字 value、图片 styles 和字体 metadata 均与源文件一致，`audit_drawio.py` 的结构与保存回读核对通过。

随后关闭旧文档页签，在新的空白文档中重新打开下载文件。三页均正常，字体已加载，标题、表格、图片保留；再次移动模块，内部元素与连线随动通过。
