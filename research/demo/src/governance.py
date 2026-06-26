"""
太一 (The One) Governance Engine (L3.1)

中立裁判:对调度申请给出 ALLOW / DENY / NEEDS_REVIEW
红线规则 + 场景约束 + 工具权限

设计要点:治理与调度物理隔离(Demo 中用独立类模拟,真实部署为独立进程)
"""
from __future__ import annotations
import fnmatch
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


class Verdict(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    NEEDS_REVIEW = "NEEDS_REVIEW"


@dataclass
class RedLine:
    """红线规则:命中即 DENY"""
    name: str
    description: str
    tool_pattern: str            # fnmatch pattern, e.g. "shell:git commit *"
    arg_blacklist: list[str]     # 参数中包含即触发


@dataclass
class ScenarioRule:
    """场景约束:命中即 NEEDS_REVIEW"""
    name: str                    # 规则名
    scenario: str                # 场景名
    description: str
    tool_pattern: str
    trigger_arg: str              # 参数包含此值触发


@dataclass
class PermitRequest:
    actor: str
    tool: str
    args: list[str]
    scenario: str
    user_id: str


@dataclass
class PermitResponse:
    verdict: Verdict
    reason: str
    evidence: str = ""
    approval_id: str | None = None


class GovernanceEngine:
    """
    治理层:中立裁判
    不可被调度层修改规则;规则以只读形式加载
    """

    def __init__(self):
        self.red_lines: list[RedLine] = []
        self.scenario_rules: list[ScenarioRule] = []
        self._load_default_rules()

    def _load_default_rules(self):
        """加载默认红线 + 场景约束"""
        # 红线:危险 shell 命令
        self.red_lines.extend([
            RedLine(
                name="dangerous_rm",
                description="禁止直接删除根目录/家目录等关键位置",
                tool_pattern="shell:rm *",
                arg_blacklist=["-rf /", "-rf ~", "-rf /*", "-rf $HOME"],
            ),
            RedLine(
                name="git_identity_override",
                description="禁止 git commit 覆盖 committer 身份",
                tool_pattern="shell:git *",
                arg_blacklist=[
                    "-c user.name=", "-c user.email=",
                    "--author=", "--config-env=", "env GIT_AUTHOR",
                ],
            ),
            RedLine(
                name="ssh_key_read",
                description="禁止读取 SSH 私钥",
                tool_pattern="file:read*",
                arg_blacklist=["id_rsa", "id_ed25519", "*.pem", ".ssh/"],
            ),
            RedLine(
                name="credential_env_leak",
                description="禁止在 shell 中打印 API Key 等敏感变量",
                tool_pattern="shell:*env*",
                arg_blacklist=["*API_KEY*", "*_TOKEN*", "*_SECRET*"],
            ),
        ])

        # 场景约束
        self.scenario_rules.extend([
            ScenarioRule(
                name="dev_git_push_review",
                scenario="dev.git",
                description="Git 推送需要人审",
                tool_pattern="shell:git push*",
                trigger_arg="push",
            ),
            ScenarioRule(
                name="ops_report_notify_review",
                scenario="ops.report",
                description="对外发布需要人审",
                tool_pattern="notify:*",
                trigger_arg="send",
            ),
            ScenarioRule(
                name="customer_refund_review",
                scenario="customer_service.refund",
                description="退款超过 100 元需要人审",
                tool_pattern="tool:refund*",
                trigger_arg="refund",
            ),
        ])

    def issue_permit(self, req: PermitRequest) -> PermitResponse:
        """
        核心接口:调度层申请,治理层裁决
        顺序: 先红线 → 再场景约束 → 默认放行
        """
        # 拼接完整调用字符串,用于模式匹配
        full_call = req.tool + " " + " ".join(req.args)

        # 1. 红线检查
        for rl in self.red_lines:
            if fnmatch.fnmatch(req.tool, rl.tool_pattern):
                for bad in rl.arg_blacklist:
                    if self._match_glob(full_call, bad):
                        return PermitResponse(
                            verdict=Verdict.DENY,
                            reason=f"触发红线规则: {rl.name}",
                            evidence=f"{rl.description}; call={full_call!r} 含禁用模式 {bad!r}",
                        )

        # 2. 场景约束
        for sr in self.scenario_rules:
            if sr.scenario == req.scenario and fnmatch.fnmatch(req.tool, sr.tool_pattern):
                # trigger_arg 可以出现在 tool 名 或 任一 arg 中
                haystack = (req.tool + " " + " ".join(req.args)).lower()
                if sr.trigger_arg.lower() in haystack:
                    return PermitResponse(
                        verdict=Verdict.NEEDS_REVIEW,
                        reason=f"场景约束: {sr.name}",
                        evidence=sr.description,
                        approval_id=f"approval_{abs(hash((req.actor, req.tool, req.scenario))) % 10**8:08d}",
                    )

        # 3. 默认放行
        return PermitResponse(
            verdict=Verdict.ALLOW,
            reason="通过红线与场景约束,默认放行",
            evidence="无触发规则",
        )

    def _match_glob(self, text: str, pattern: str) -> bool:
        """支持简单 glob 通配(包含 *)的子串匹配"""
        if "*" in pattern:
            # 把 glob 转正则
            regex = re.escape(pattern).replace(r"\*", ".*")
            return bool(re.search(regex, text))
        return pattern in text
