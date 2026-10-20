# 高清素材与原生 PPT 结构

本流程不做位图矢量化。文字/框/表格/箭头用原生对象，图像素材无损抠取或 API 生成，再作为独立高清图片对象嵌入。

## 1. 抠图

```powershell
python scripts/crop_assets.py reference.png extracted/robot.png --crop 799 694 276 260
```

参数 `--crop X Y W H` 是源像素坐标。输出无损 RGBA PNG，不改变尺寸、调色板或边缘。保留原文件和 `.source.json`。

要把电脑屏幕文字改成原生文本，可以在抠取外壳时实际清除屏幕内部：

```powershell
python scripts/crop_assets.py reference.png extracted/laptop.png --crop 380 238 279 187 --clear-rect 28 13 224 146
```

`--clear-rect` 坐标相对于裁图，不是全图；可重复指定。这里只移除应被独立重建的屏幕内容，不能拿来随便删掉图标结构。白底素材可保留白底；需要透明抠取时用真实透明度或原像素蒙版，不重绘轮廓。

## 2. 原生底稿

`build_native_layout.py` 用 `scene.json` 建立原生背景、框、文本、表格单元格及流程关系。支持 rect/ellipse/text/path/connector。路径只用于结构性线条、箭头或实际源矢量；不得用它重画位图插画。

底稿在新文件生成，不用于覆盖用户已手调稿。字体、点坐标和像素换算由 scene 明确。

```text
目标宽度 Pw 点，原图 W 像素：s = Pw / W
left=x*s, top=y*s, width=w*s, height=h*s
```

保持一个统一比例，禁止 X/Y 各自拉伸。

## 3. 独立素材清单

```json
{
  "slide":1,
  "before_text":true,
  "asset_policy":"no-tracing",
  "assets":[
    {"name":"IMAGE_AGENT","file":"extracted/robot.png","box_pt":[452,392,156,147],"source_type":"source-crop","crop_record":"extracted/robot.source.json"}
  ]
}
```

素材路径相对于清单，`box_pt` 用点，图片等比 contain 放置。目标框应与源裁片比例一致，避免无谓留白。插入脚本保留原始 PNG/JPEG 字节与透明度，不重采样、不转换路径。

```powershell
python scripts/build_native_layout.py scene.json layout.pptx
python scripts/insert_assets_pptx.py layout.pptx assets.json with-assets.pptx
```

脚本产生 `.assets.json`，记录对象名、实际位置、嵌入哈希、像素与有效 PPI。图片依然是图片对象；不要称它能编辑内部节点。

`before_text:true` 放在第一个原生文本之前。背景先建立，标题/正文等原生文字在素材之上；屏幕内控件等需要更前景的原生元素置于第一个文本之后。检查白底裁片有没有遮住旁边文字/箭头，不使用整图截图兜底。

## 4. 模块和连线

`module_groups` 按最终位置把框、文字和图片归为语义模块。`bind_connections` 为直线指定组，工具使用组内连接点绑定；副本中移动模块验证。

```powershell
python scripts/group_and_connect.py with-assets.pptx scene.json figure.pptx
```

复杂曲线和任意折线由原生自由曲线表达，能单独编辑；不因此宣称端点能实时跟随模块。确有要求时，在目标应用用受支持的连接器绑定并实际移动。

## 5. 真正的源矢量

有用户提供的原生 SVG/矢量源时，可由目标 Office 应用保留或转换其已有路径。这是保留真实源路径，不是从位图产生曲线。源 SVG 要保存来源，且不能含字体轮廓冒充可编辑文字。本技能不附带位图转路径工具。

## 6. 依赖

裁剪/插入：Pillow、lxml；字体匹配另需 numpy、opencv-python、fonttools。生成遵循当前 imagegen 工具合同；不自带/收集 API 密钥。
