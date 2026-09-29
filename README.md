# 地块叠置裁决 API（Parcel Overlay Adjudicator）

基于 **Python + FastAPI + Shapely** 的本地服务：接收 1～12 块带优先级的地块
（平面 GeoJSON Polygon / MultiPolygon），按优先级分层裁决叠置关系，返回每块地的
面积台账与归并后的冲突区。完全本地运行，**不需要任何外部账号或网络服务**。

## 快速开始

只需一条命令（需要本机已安装 Docker 与 Docker Compose）：

```bash
docker compose up --build
```

服务启动后监听 **http://localhost:8000** ：

- 接口：`POST http://localhost:8000/resolve`
- 交互式文档（Swagger UI）：http://localhost:8000/docs
- 健康检查：http://localhost:8000/healthz

停止服务：`docker compose down`。

## 裁决规则

1. 所有地块按 `priority` **降序**分层（相同优先级为同一层），逐层扣除已处理区域
   （更高层地块的并集，含更高层产生的冲突区）。
2. 当前层的**余地**（本层地块未被更高层覆盖的部分）中：
   - 恰好被 **1** 块本层地块覆盖 → 归该唯一覆盖者（获分）；
   - 被 **≥2** 块本层地块覆盖 → 按**完全相同的参与 id 集合**归并为冲突区，
     冲突区**不再交给**更低优先级的地块。
3. **共边、共点不算冲突**（零面积重叠不产生冲突区）。
4. 孔洞（hole）与多部件（MultiPolygon）均被正确处理；每块地满足面积守恒：
   `original_area = awarded_area + conflict_area + covered_by_higher_area`。

## 请求

`POST /resolve`，Content-Type: `application/json`

```json
{
  "parcels": [
    {"id": "A", "priority": 2, "geometry": {"type": "Polygon", "coordinates": [[[0,0],[4,0],[4,4],[0,4],[0,0]]]}},
    {"id": "B", "priority": 2, "geometry": {"type": "Polygon", "coordinates": [[[2,0],[6,0],[6,4],[2,4],[2,0]]]}},
    {"id": "C", "priority": 1, "geometry": {"type": "Polygon", "coordinates": [[[1,1],[5,1],[5,3],[1,3],[1,1]]]}}
  ]
}
```

- `parcels`：数组，长度 **1～12**。
- `id`：字符串或整数，**全局唯一**。
- `priority`：**整数**（可为负；布尔值、浮点、字符串均拒绝）。数值越大优先级越高。
- `geometry`：平面 GeoJSON **Polygon 或 MultiPolygon** 几何对象（裸几何，不接受
  Feature/FeatureCollection）。坐标可为 3D，Z 值在计算中被忽略。

## 响应（200）

```json
{
  "parcels": [
    {
      "index": 0,
      "id": "A",
      "priority": 2,
      "original_area": 16.0,
      "covered_by_higher_area": 0.0,
      "conflict_area": 8.0,
      "awarded_area": 8.0,
      "awarded_geometry": {"type": "Polygon", "coordinates": ["..."]}
    }
  ],
  "conflicts": [
    {
      "parcel_ids": ["A", "B"],
      "area": 8.0,
      "geometry": {"type": "Polygon", "coordinates": ["..."]}
    }
  ]
}
```

`parcels` 按输入顺序返回，每项：

| 字段 | 含义 |
| --- | --- |
| `index` | 输入数组中的下标（从 0 开始） |
| `id` / `priority` | 原样回显 |
| `original_area` | 原始面积 |
| `covered_by_higher_area` | 被更高优先级层（含其冲突区）遮盖的面积 |
| `conflict_area` | 同层冲突面积 |
| `awarded_area` | 获分面积 |
| `awarded_geometry` | 获分几何（Polygon/MultiPolygon）；**无获分时为 `null`** |

`conflicts` 为冲突区列表，每项：

| 字段 | 含义 |
| --- | --- |
| `parcel_ids` | 参与冲突的地块 id 集合（按输入下标排序） |
| `area` | 冲突区面积 |
| `geometry` | 冲突区几何（Polygon/MultiPolygon） |

面积单位与输入坐标单位一致（平面坐标，例如米制投影下为平方米）。

## 输入校验与错误（400）

空面、自交、非面几何等非法输入返回 HTTP 400，并在 `errors` 中给出**输入下标**
（`index`，从 0 开始；请求级问题为 `null`）。所有错误一次性返回：

```json
{
  "detail": "invalid input",
  "errors": [
    {"index": 1, "code": "non_polygonal_geometry", "message": "geometry type must be Polygon or MultiPolygon, got 'Point'"}
  ]
}
```

| code | 含义 |
| --- | --- |
| `invalid_payload` | 请求体不是 `{"parcels": [...]}` 结构 |
| `invalid_parcel_count` | 地块数量不在 1～12 |
| `invalid_parcel` | 地块不是对象 |
| `invalid_id` / `duplicate_id` | id 缺失/类型非法/重复 |
| `invalid_priority` | priority 不是整数 |
| `non_polygonal_geometry` | 几何不是 Polygon/MultiPolygon |
| `empty_geometry` | 空面（无坐标或空部件） |
| `self_intersecting_geometry` | 自交或环结构非法（GEOS 判定无效） |
| `invalid_geometry` | 坐标缺失、含非有限数值或无法解析 |

## 本地开发与测试

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest                 # 运行测试（含面积守恒、孔洞、多部件、共边共点等用例）
uvicorn app.main:app --reload   # 本地起服务于 http://localhost:8000
```

## 实现说明

- 对所有地块几何做带标签的平面划分（原子单元 + 覆盖它的地块集合），再按
  "单元内最高优先级"裁决：唯一最高者获分，并列最高（≥2）归并为冲突区，其余
  低优先级地块记为被高层遮盖。共边/共点只产生零面积单元，天然不会形成冲突。
- 几何运算由 Shapely 2.x（GEOS）完成，叠置结果中的线/点碎片被剔除，仅保留面。
- 获分几何与冲突区几何两两内部不相交，其并集等于输入并集（面积守恒，浮点
  容差约 1e-9）。
