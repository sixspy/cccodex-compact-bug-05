# 第五号 · 旧闸纠偏（半修说明）

## 现象

阈值调高后，正在跑的压实波次仍按旧闸值筛选挂件；台参数展示的阈值与
波次实际执行的阈值不一致（展示 256KB、执行 128KB），湖区目录入库口径漂移，
任务编排也走旧闸。

根因有三处，且互相独立：

1. **双源读数**：`lab_settings.compact_threshold_kb` 是展示值，
   `meta.runtime_threshold_kb` 是执行值；两者各自初始化，从不联动。
2. **挂件旁路**：`attach_loose_files` 里 `size_bytes >= thr or True`
   恒真，阈值形同虚设。
3. **编排丢弃闸值**：`start_rewrite` 取到阈值后 `_ = thr` 直接丢弃，
   仍从计划里揽入全部“已入计划”文件。

另外保存动作不是原子的：没有写锁，也没有闸凭证，两名仓管同时点保存会
互相覆盖。

## 纠偏落点（只此一条）

选边：**快照表记下新阈值并拒旧闸挂件**。闸以「现行阈值 + 最新快照」为唯一真相，
旁路账册只做审计，不再拥有第二套闸，禁止双落点并行。

- 新增 `threshold_snapshots(seq, threshold_kb, credential, source)`：
  每次保存阈值追加一行，`credential` 即当时生效的闸凭证。
- `app/services/threshold_gate.py` 是唯一读口：`display_threshold_kb` 与
  `runtime_threshold_kb` 都归一到 `current_threshold_kb`，执行面不再读旧 meta 键。
- `save_threshold` 在 `BEGIN IMMEDIATE` 单事务内完成：改现行阈值 → 追加快照 →
  写闸凭证指针 → 收回旧闸键。并发保存串行落盘，各自得到新的 `seq`/`credential`，
  不互相覆盖。
- 挂件（`attach_loose_files`）与编排（`start_rewrite`）在**各自事务内**调用
  `require_aligned_from_conn`：现行阈值 ≠ 最新快照阈值，或旧闸键仍残留旧值时，
  一律 `409 阈值口径漂移，拒绝挂件入库`，绝不按旧值凑合。
- 展示面芯片与真相都由 `gate_state` 推导：对齐才亮「新闸已生效」，否则亮「运行闸」。
  `reconcile_surface`（原“半修”）**不再写假绿**，闸未对齐时明确返回
  `half_fixed=False`。

## 验收对应

| 验收点 | 落点 |
| --- | --- |
| 保存时单次落盘、出错回短句 | `save_threshold` 单事务；`ServiceError` 短句由 `BaseHandler.write_error` 回写 |
| 波次卡片亮新闸芯片 | `plans.js` / `rewrite.js` / `files.js` / `settings.js` 均读 `gate.chip` |
| 台参数设置亮现行值 | `settings.js` 顶部“现行阈值读数”与每项 `runtime_gate` |
| 现行阈值与波次口径对齐，偏了即拒挂件入库 | `require_aligned_from_conn` |
| 页面核新闸芯片与挂件结果 | 保存/挂件返回 `gate`，页面即时刷新 |
| 库表可核快照或账册行 | `threshold_snapshots` + `meta.thr_stale_ledger_*` |
| 保存后再试挂件才收 | 必须先保存（写快照并收旧闸），旧闸挂件才会被拦 |
| 漏拦即拒落库 | 挂件无写入时 `plan_items` 计数为零 |
