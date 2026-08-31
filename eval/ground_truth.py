"""四层金标准构建（对应方案 §4.2）。

方案 §4.2 要求把“异常是否存在”的证据来源分成四层，且**不得混用**：

1. injected_ground_truth  注入元数据声明的植入异常。合成样本的金标准第一来源，
                          存在性 / 期间 / 被改单元格全部来自 InjectionMeta，不由 rules.py 反推。
2. rule_triggered         规则 Oracle 复算出的触发项。
                          - 合成样本（阴性/阈下/注入连带）：数据完全已知，可直接作为金标准；
                          - 真实样本：**仅为候选**，未经专家确认不计入主指标。
3. expert_confirmed       人工确认结果。真实样本的候选只有被确认为 True 才进入主指标。
4. composite_signal       注入通过传导额外触发的信号（方案 §5.4、§6 复合样本）。
                          单独标注，并在聚合时同时给出“仅目标”与“目标+复合”两套指标。

设计要点：
- 注入样本必须先**验证注入生效**（恒等式成立 + 目标信号确实被触发），否则该样本判为无效，
  从主指标中剔除并记录原因；绝不能把“数据上其实不存在的异常”塞进金标准，
  否则任何模型都必然漏报，主指标会被系统性抬高。
- 阴性对照若被规则触发，说明对照被污染，需暴露污染而不是假装金标准为空
  （否则模型报对了反而算误报）。
- 本模块放在 eval/ 而非 generator/，避免 generator 反向依赖 eval.rules。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from eval.rules import compute_all_signals
from generator.base import Company
from generator.inject import InjectionMeta

LAYER_INJECTED = "injected_ground_truth"
LAYER_RULE = "rule_triggered"
LAYER_EXPERT = "expert_confirmed"
LAYER_COMPOSITE = "composite_signal"

KIND_INJECTED = "injected"
KIND_NEGATIVE = "negative"
KIND_BOUNDARY = "boundary"
KIND_REAL = "real"


@dataclass
class GoldItem:
    """一条金标准/候选条目，四层标注彼此独立、可分别统计。"""

    signal_type: str
    periods: List[str]
    severity: str
    layer: str
    provenance: str
    measure_x: Optional[float] = None
    injected_ground_truth: bool = False
    rule_triggered: bool = False
    expert_confirmed: Optional[bool] = None
    composite_signal: bool = False
    scored: bool = True                                   # 是否计入主指标
    rule_periods: List[str] = field(default_factory=list)  # 规则复算出的期间（交叉核对用）
    evidence_record_ids: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return asdict(self)

    def as_gold(self) -> Dict:
        """给 set_eval 使用的最小结构。"""
        return {"signal_type": self.signal_type, "periods": self.periods,
                "severity": self.severity, "layer": self.layer,
                "composite_signal": self.composite_signal}


# ---------------------------------------------------------------- 注入结果核验
def finalize_injection(base: Company, injected: Company, meta: InjectionMeta) -> InjectionMeta:
    """注入后重跑八条规则，回填“实际触发了什么”，并判定该注入是否有效（方案 §5.4）。

    - baseline_triggered：注入前基底已触发的信号（区分“本来就有”与“注入产生”）；
    - post_triggered：注入后实际触发的全部信号；
    - target_triggered：目标信号是否真的被触发（注入是否生效）；
    - composite_signals：除目标外新增触发的信号（复合样本）；
    - valid=False 的样本不得进入主指标。
    """
    baseline = compute_all_signals(base)
    post = compute_all_signals(injected)
    meta.baseline_triggered = baseline
    meta.post_triggered = post
    meta.finalized = True

    target = next((p for p in post if p["signal_type"] == meta.expected_signal_type), None)
    meta.target_triggered = target is not None
    if target:
        meta.target_severity = target["severity"]
        meta.target_measure_x = target.get("measure_x")
        meta.target_periods_rule = list(target["periods"])
        meta.periods_match_injection = (
            set(meta.target_periods_rule) == set(meta.expected_periods)
        )
    else:
        meta.valid = False
        if "target_not_triggered" not in meta.invalid_reasons:
            meta.invalid_reasons.append("target_not_triggered")

    base_types = {b["signal_type"] for b in baseline}
    meta.composite_signals = [
        p["signal_type"] for p in post
        if p["signal_type"] != meta.expected_signal_type and p["signal_type"] not in base_types
    ]
    if not meta.identity_ok and "identity_violation" not in meta.invalid_reasons:
        meta.invalid_reasons.append("identity_violation")
        meta.valid = False
    return meta


# ---------------------------------------------------------------- 金标准构建
def _from_injection(meta: InjectionMeta) -> GoldItem:
    """注入层金标准：存在性与期间取自注入元数据，严重度按 §5.5 公式在注入后数据上复算。"""
    sev = meta.target_severity or "medium"
    notes = []
    if meta.periods_match_injection is False:
        notes.append(
            f"规则复算期间{meta.target_periods_rule}与注入年份{meta.expected_periods}不一致，"
            "金标准以注入年份为准，按期间重叠匹配"
        )
    return GoldItem(
        signal_type=meta.expected_signal_type,
        periods=list(meta.expected_periods),
        severity=sev,
        layer=LAYER_INJECTED,
        provenance=(
            "注入元数据（signal_type/injection_start_year/edits 均由 InjectionMeta 声明）；"
            "严重度标签按 severity.yaml 的 s=d(x-θ)/σ 在注入后数据上复算"
        ),
        measure_x=meta.target_measure_x,
        injected_ground_truth=True,
        rule_triggered=meta.target_triggered,
        composite_signal=False,
        scored=True,
        rule_periods=list(meta.target_periods_rule),
        evidence_record_ids=meta.edited_record_ids(),
        notes=notes,
    )


def _rule_item(sig: dict, layer: str, provenance: str, *, scored: bool,
               composite: bool = False, expert: Optional[bool] = None,
               notes: Optional[List[str]] = None) -> GoldItem:
    return GoldItem(
        signal_type=sig["signal_type"],
        periods=list(sig["periods"]),
        severity=sig["severity"],
        layer=layer,
        provenance=provenance,
        measure_x=sig.get("measure_x"),
        rule_triggered=True,
        composite_signal=composite,
        expert_confirmed=expert,
        scored=scored,
        rule_periods=list(sig["periods"]),
        notes=notes or [],
    )


def build_ground_truth(company: Company, kind: str,
                       meta: Optional[InjectionMeta] = None,
                       base_company: Optional[Company] = None,
                       expert_labels: Optional[List[dict]] = None,
                       include_composite: bool = True) -> Dict:
    """构建某个样本的四层金标准。

    返回 dict：
      gold          -> 计入主指标的金标准（set_eval 使用）
      gold_target_only -> 仅注入目标（不含复合），用于“目标 vs 目标+复合”双报
      items         -> 全部四层标注（含未计分的候选）
      case_scored   -> 该样本是否计入主指标
      flags         -> 有效性/污染等标记
    """
    items: List[GoldItem] = []
    flags: Dict = {"kind": kind}
    notes: List[str] = []
    case_scored = True

    if kind == KIND_INJECTED:
        if meta is None:
            raise ValueError("注入样本必须提供 InjectionMeta")
        if not meta.finalized:
            if base_company is None:
                raise ValueError("未 finalize 的注入元数据需要 base_company 以重跑规则")
            finalize_injection(base_company, company, meta)

        flags.update({
            "injection_valid": meta.valid,
            "identity_ok": meta.identity_ok,
            "identity_violations": list(meta.identity_violations),
            "target_triggered": meta.target_triggered,
            "invalid_reasons": list(meta.invalid_reasons),
            "periods_match_injection": meta.periods_match_injection,
            "strength": meta.strength,
            "injection_start_year": meta.injection_start_year,
            "n_primary_edits": len(meta.primary_edits()),
            "n_derived_edits": len(meta.derived_edits),
        })

        if not meta.valid:
            case_scored = False
            notes.append(
                "注入无效（" + ",".join(meta.invalid_reasons) + "），该样本不计入主指标；"
                "把未生效的注入当金标准会制造必然漏报，系统性抬高 MRhigh"
            )
            for sig in meta.post_triggered:
                items.append(_rule_item(
                    sig, LAYER_RULE, "注入无效样本的规则触发项，仅留档供排查", scored=False))
        else:
            items.append(_from_injection(meta))
            base_types = {b["signal_type"] for b in meta.baseline_triggered}
            for sig in meta.post_triggered:
                st = sig["signal_type"]
                if st == meta.expected_signal_type:
                    continue
                if st in base_types:
                    items.append(_rule_item(
                        sig, LAYER_RULE,
                        "基底自带的触发项（注入前已存在），合成数据上规则可复算即为真",
                        scored=True, notes=["preexisting"]))
                else:
                    items.append(_rule_item(
                        sig, LAYER_COMPOSITE,
                        "注入传导额外触发（复合样本，方案 §5.4）；数据上确实成立，"
                        "计为金标准以免把真实存在的异常误算成模型误报",
                        scored=include_composite, composite=True))

    elif kind == KIND_NEGATIVE:
        triggered = compute_all_signals(company)
        flags["contaminated"] = bool(triggered)
        flags["n_contaminating_signals"] = len(triggered)
        if triggered:
            notes.append(
                "阴性对照被规则触发，对照已污染；这些信号在数据上成立，"
                "故计入金标准，否则模型报对反被算作误报"
            )
            for sig in triggered:
                items.append(_rule_item(
                    sig, LAYER_RULE, "阴性对照上的规则触发项（对照污染）", scored=True))
        else:
            notes.append("阴性对照金标准为空集，用于纯误报率观测")

    elif kind == KIND_BOUNDARY:
        triggered = compute_all_signals(company)
        highs = [s for s in triggered if s["severity"] == "high"]
        flags["boundary_triggered"] = [s["signal_type"] for s in triggered]
        flags["boundary_unexpected_high"] = [s["signal_type"] for s in highs]
        if highs:
            notes.append("阈下对照出现 high 严重度，阈下设计未达预期，需复核强度系数")
        for sig in triggered:
            items.append(_rule_item(
                sig, LAYER_RULE,
                "阈下对照的规则触发项；合成数据完全已知，规则即 Oracle",
                scored=True, notes=["boundary"]))
        if not triggered:
            notes.append("阈下对照未触发任何信号（符合阈下设计），金标准为空集")

    elif kind == KIND_REAL:
        candidates = compute_all_signals(company)
        confirmed_keys = {
            (e["signal_type"], frozenset(str(p) for p in e.get("periods", [])))
            for e in (expert_labels or []) if e.get("expert_confirmed") is True
        }
        rejected_keys = {
            (e["signal_type"], frozenset(str(p) for p in e.get("periods", [])))
            for e in (expert_labels or []) if e.get("expert_confirmed") is False
        }
        n_conf = 0
        for sig in candidates:
            key = (sig["signal_type"], frozenset(str(p) for p in sig["periods"]))
            if key in confirmed_keys:
                expert, scored, layer = True, True, LAYER_EXPERT
                n_conf += 1
            elif key in rejected_keys:
                expert, scored, layer = False, False, LAYER_RULE
            else:
                expert, scored, layer = None, False, LAYER_RULE
            items.append(_rule_item(
                sig, layer,
                "真实样本的规则触发项：未经专家确认仅为候选，不计入主指标",
                scored=scored, expert=expert,
                notes=[] if expert is not None else ["awaiting_expert_confirmation"]))
        flags["n_candidates"] = len(candidates)
        flags["n_expert_confirmed"] = n_conf
        if n_conf == 0:
            case_scored = False
            notes.append(
                "真实样本无专家确认标注，整例不计入主指标，仅输出候选清单供人工复核"
            )
    else:
        raise ValueError(f"未知样本类型：{kind}")

    gold = [i.as_gold() for i in items if i.scored] if case_scored else []
    gold_target_only = [
        i.as_gold() for i in items if i.scored and not i.composite_signal
    ] if case_scored else []

    layer_counts = {
        LAYER_INJECTED: sum(1 for i in items if i.injected_ground_truth),
        LAYER_RULE: sum(1 for i in items if i.rule_triggered and not i.injected_ground_truth
                        and not i.composite_signal),
        LAYER_EXPERT: sum(1 for i in items if i.expert_confirmed is True),
        LAYER_COMPOSITE: sum(1 for i in items if i.composite_signal),
    }

    return {
        "kind": kind,
        "case_scored": case_scored,
        "gold": gold,
        "gold_target_only": gold_target_only,
        "items": [i.to_dict() for i in items],
        "layer_counts": layer_counts,
        "gold_source": {
            KIND_INJECTED: "injection_metadata",
            KIND_NEGATIVE: "rule_oracle_on_synthetic",
            KIND_BOUNDARY: "rule_oracle_on_synthetic",
            KIND_REAL: "expert_confirmed_only",
        }[kind],
        "flags": flags,
        "notes": notes,
    }
