"""评测打分函数。

每个 scorer 接收 (task, observation) → 返回 dict：
  {
    "score": float,        # 0-1
    "passed": bool,        # 是否过线
    "details": dict,       # 解释（哪些匹配了/没匹配）
  }

observation 的形状（由 run_eval 收集）：
  {
    "tools_called": [{"name": "run_command", "command": "query-metrics ...", "raw_input": {...}}],
    "final_text": "...合并后的 LLM 文本...",
    "outputs": [{"action": "text", "content": "..."}, {"action": "chart", ...}],
  }
"""
from __future__ import annotations


def score_tool_recall(task: dict, observation: dict) -> dict:
    """Tool Recall: agent 是否调用了期望的子命令，参数串里是否含必备子串。"""
    expected = task.get("expected", {}).get("tools_must_call", [])
    if not expected:
        return {"score": 1.0, "passed": True, "details": {"reason": "no expected tools"}}

    # 把 agent 实际调用过的 command 字符串都收集出来
    actual_commands: list[str] = []
    for call in observation.get("tools_called", []):
        # run_command 的 input 是 {"command": "query-metrics --metrics click ..."}
        cmd_str = ""
        raw = call.get("raw_input") or {}
        if isinstance(raw, dict):
            cmd_str = str(raw.get("command", ""))
        actual_commands.append(cmd_str)

    matched = []
    missing = []
    for exp in expected:
        exp_name = exp.get("name", "")
        must_contain = exp.get("args_must_contain", []) or []

        hit = False
        for cmd in actual_commands:
            # 命令的第一 token 应等于期望子命令名
            first_token = cmd.split(maxsplit=1)[0] if cmd else ""
            if first_token != exp_name:
                continue
            if all(needle in cmd for needle in must_contain):
                hit = True
                break

        if hit:
            matched.append(exp_name)
        else:
            missing.append({"name": exp_name, "args_must_contain": must_contain})

    score = len(matched) / len(expected)
    return {
        "score": score,
        "passed": score >= 1.0,
        "details": {
            "matched": matched,
            "missing": missing,
            "actual_commands": actual_commands,
        },
    }


def score_answer_contains(task: dict, observation: dict) -> dict:
    """简易 EM：最终文本是否包含所有期望子串。"""
    needles = task.get("expected", {}).get("answer_contains", [])
    if not needles:
        return {"score": 1.0, "passed": True, "details": {"reason": "no answer_contains"}}

    text = observation.get("final_text", "") or ""
    # 也把 send_to_user(action="text") 的内容算上（虽然新协议不再用 text action）
    for out in observation.get("outputs", []):
        if isinstance(out, dict) and out.get("action") == "text":
            text += "\n" + str(out.get("content", ""))

    found = [n for n in needles if n in text]
    missing = [n for n in needles if n not in text]
    score = len(found) / len(needles)
    return {
        "score": score,
        "passed": score >= 1.0,
        "details": {
            "found": found,
            "missing": missing,
        },
    }


def score_answer_numerical(task: dict, observation: dict) -> dict:
    """数值精确匹配（如果题目设了 answer_numerical 字段）。"""
    expected = task.get("expected", {}).get("answer_numerical")
    if expected is None:
        return {"score": 1.0, "passed": True, "details": {"reason": "no answer_numerical"}}

    text = observation.get("final_text", "") or ""
    needle = str(expected)
    passed = needle in text
    return {
        "score": 1.0 if passed else 0.0,
        "passed": passed,
        "details": {"expected": expected, "found": passed},
    }


def aggregate(*sub_scores: dict) -> dict:
    """加权求和（当前等权）。score = mean(sub_scores)；passed = 全部 pass。"""
    if not sub_scores:
        return {"score": 0.0, "passed": False, "details": {}}
    score = sum(s["score"] for s in sub_scores) / len(sub_scores)
    passed = all(s["passed"] for s in sub_scores)
    return {"score": score, "passed": passed, "details": [s["details"] for s in sub_scores]}
