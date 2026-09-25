# 本地地块叠置裁决 API

基于 Python + FastAPI + Shapely 的本地地块（parcel）叠置裁决服务：接收 1～12 块平面地块，
按优先级逐层裁决，输出每块地的原面积、被高层遮盖面积、同层冲突面积、获分面积与获分几何，
以及全部冲突区的参与方、面积和几何。**纯本地运行，不需要任何外部账号或网络服务。**

## 一条命令启动

```bash
docker compose up --build
```

启动后访问：

- 服务地址：<http://localhost:8000>
- 健康检查：<http://localhost:8000/healthz>
- 交互式接口文档（Swagger UI）：<http://localhost:8000/docs>
- OpenAPI Schema：<http://localhost:8000/openapi.json>

> 需要 Docker 20.10+ 与 Docker Compose v2。后台运行可用 `docker compose up --build -d`，
> 停止用 `docker compose down`。

## 接口

### `POST /resolve`

请求体：

```json
{
  "parcels": [
    {
      "id": "A",
      "priority": 3,
      "geometry": {
        "type": "Polygon",
        "coordinates": [[[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]]]
      }
    },
    {
      "id": "B",
      "priority": 2,
      "geometry": {
        "type": "MultiPolygon",
        "coordinates": [
          [[[2, 2], [6, 2], [6, 6], [2, 6], [2, 2]]]
        ]
      }
    }
  ]
}
```

字段说明：

| 字段 | 说明 |
| --- | --- |
| `parcels` | 1～12 块地，数组顺序即输入下标（从 0 开始，用于错误定位） |
| `id` | 地块唯一标识，字符串或整数，本批次内不可重复 |
| `priority` | 整数优先级，**数值越大越优先**；相同 priority 视为同一层 |
| `geometry` | 平面 GeoJSON `Polygon` 或 `MultiPolygon`（可含孔洞、多部件） |

成功响应 `200`：

```json
{
  "parcels": [
    {
      "id": "A",
      "original_area": 16.0,
      "covered_area": 0.0,
      "conflict_area": 0.0,
      "awarded_area": 16.0,
      "awarded_geometry": { "type": "Polygon", "coordinates": [] }
    },
    {
      "id": "B",
      "original_area": 16.0,
      "covered_area": 4.0,
      "conflict_area": 0.0,
      "awarded_area": 12.0,
      "awarded_geometry": { "type": "Polygon", "coordinates": [] }
    }
  ],
  "conflicts": []
}
```

每块地返回：

| 字段 | 含义 |
| --- | --- |
| `original_area` | 输入几何的原面积 |
| `covered_area` | 被**更高优先级**层（无论唯一还是冲突区）遮盖的面积 |
| `conflict_area` | 本地块参与的、与同层其他地块之间的冲突面积之和 |
| `awarded_area` | 最终唯一判归本地块的面积 |
| `awarded_geometry` | 获分几何（GeoJSON Polygon/MultiPolygon）；没有获分时为 `null` |

`conflicts` 数组每项为一个冲突区（按面积降序）：

```json
{
  "participant_ids": ["A", "B"],
  "area": 4.0,
  "geometry": { "type": "Polygon", "coordinates": [] }
}
```

- `participant_ids`：冲突参与方的 id（至少两个）；
- 被**完全相同的参与 id 集合**覆盖的所有碎片会归并为同一个冲突区（可能是 MultiPolygon）；
- 冲突区不再交给任何更低优先级地块。

错误响应 `422`：几何校验失败时带 0 基输入下标，例如：

```json
{ "error": "invalid_parcel", "index": 1, "message": "几何无效（自交或环不闭合等）：..." }
```

## 裁决规则

1. **分层**：按 `priority` 降序处理；同一块地不可跨层。
2. **层间扣除**：每层先把每块地减去“所有更高优先级已处理区域”，得到本地块的**余地**。
3. **同层裁决**：把本层全部余地叠加、在交点处切开形成原子面：
   - 原子面内部只被一块地覆盖 → 判归该地块（唯一获分）；
   - 原子面内部被至少两块地覆盖 → 冲突区，按**参与 id 集合完全相同**的原则归并；
4. **下沉阻断**：本层所有余地（唯一获分区 + 冲突区）整体并入已处理区域，低优先级地块无法进入。
5. **边界规则**：仅共边或共点的地块不会形成冲突（共享边界没有面内部重叠）。
6. **面积守恒**：对每块地恒有
   `原面积 = 被高层遮盖面积 + 同层冲突面积 + 获分面积`；
   全局范围内输入并集的每一点，恰好属于一个唯一获分区或一个冲突区。

孔洞（Polygon 内环）与 MultiPolygon 多部件均正确处理；GeoJSON 坐标按平面坐标处理，
面积单位即输入坐标单位的平方（服务不做投影/CRS 转换，请传入已投影到平面坐标系的几何）。

## 输入限制

- 地块数量 1～12；`id` 唯一；`priority` 为整数（不接受 `true/false`）。
- 几何必须是 `Polygon` / `MultiPolygon`；不接受 `Point`、`LineString`、`GeometryCollection`、
  `Feature` / `FeatureCollection`。
- 空面、零面积面、自交或其他无效几何一律以 `422` 拒绝，并在 `index` 中给出输入下标。

## 调用示例

```bash
curl -s -X POST http://localhost:8000/resolve \
  -H 'Content-Type: application/json' \
  -d '{
    "parcels": [
      {"id": "A", "priority": 2, "geometry": {"type": "Polygon",
        "coordinates": [[[0,0],[2,0],[2,2],[0,2],[0,0]]]}},
      {"id": "B", "priority": 1, "geometry": {"type": "Polygon",
        "coordinates": [[[1,0],[3,0],[3,2],[1,2],[1,0]]]}}
    ]
  }'
```

## 本地开发（不用 Docker）

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --port 8000
pytest -q
```

测试包含同层冲突、层间遮盖、共边/共点、三方冲突归并、孔洞、MultiPolygon、
随机矩形面积守恒与互斥性校验，以及各类非法输入的下标报错。

## 项目结构

```
app/
  __init__.py
  geometry.py   # GeoJSON 解析/校验、面规范化、同层原子面切分与标注
  resolver.py   # 分层扣除 + 唯一/冲突裁决核心算法
  main.py       # FastAPI 路由、Pydantic 模型、错误处理
tests/
  test_resolver.py
Dockerfile
docker-compose.yml
requirements.txt
```
