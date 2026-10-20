# 场景、素材和 EEGAgent Live 示例

## 1. 职责分开

`scene.json` 管原生文字、框、流程线、字体、分组；`assets.json` 管独立图片素材。场景里声明 `asset_policy: no-tracing`。

```json
{
  "title":"EEGAgent Live",
  "canvas":{"width":1698,"height":1115,"unit":"px","width_pt":960},
  "asset_policy":"no-tracing",
  "asset_mode":"source-crop",
  "elements":[
    {"id":"FRAME","type":"rect","box":[30,153,288,301],"style":{"fill":"#FFFFFF","stroke":"#60218A","stroke_width":2.2,"radius":18}},
    {"id":"TITLE","type":"text","text":"OpenBCI设备","box":[39,159,270,52],"style":{"font":"Roboto","font_ea":"Microsoft YaHei","font_size":30,"bold":true,"color":"#351056","align":"center"}}
  ],
  "expected_texts":["OpenBCI设备"],
  "module_groups":[{"name":"MODULE_DEVICE","box":[30,153,288,301]}]
}
```

像素坐标一次等比换算为点。`elements` 顺序就是图层顺序，也可显式使用 z。不能因几何相近就猜科研关系。

## 2. 来源

抠取素材 `source_type=source-crop`，附 `crop_record`；API 素材 `source_type=api-generated-original`，附 `generation_record`。生成后仍保留像素，不描摹。

所有可见文字列入 `expected_texts`，同词多处出现也分别计数。`expected_groups` 记录语义模块。绑定后的直线可声明：

```json
{"name":"DEVICE_TO_GUI","from":"PORT_DEVICE_TO_GUI_from","to":"PORT_DEVICE_TO_GUI_to"}
```

## 3. 示例关系

OpenBCI 设备 → 官方采集软件 → LSL 数据流 → Python 在线处理。Python 输出实时结果并保存原始数据/事件；Python → EEGAgent 传递结构化结果，EEGAgent → Python 传递实验设置与分析请求；EEGAgent 输出解释、比较与报告。

识别方法映射和 Agent 反馈不要混淆。替图标时不改箭头方向；只改此图时不修改论文其他章节。

示例最终由 24 个独立裁图、24 个原生文本框和 7 个语义模块组成。图片内部不是原生节点；模块和图片可单独移动/替换。此数据只描述已完成的示例，不规定所有任务的组件数量。
