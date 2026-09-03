from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from skill_auto.runner import (
    detect_skill_execution,
    detect_skill_trigger,
    extract_output_artifacts,
    aggregate_output_artifacts,
    extract_history_answer,
    generate_test_cases_batch,
    has_local_dependency_marker,
    has_task_result,
    is_terminal_history_item,
    is_rate_limited_observation,
    is_history_answer_complete,
    latest_history_item,
    latest_history_run_status,
    normalize_model_demo_case,
    parse_is_generating,
    parse_batch_demo_case_response,
    preferred_observation_failure,
    redact_observation_secrets,
    summarize_semantic_results,
    valid_demo_case,
)
from skill_auto.semantic_evaluator import apply_semantic_result, normalize_semantic_output


class RunnerJudgementTest(unittest.TestCase):
    def test_dev_expert_structured_diagnosis_is_success(self) -> None:
        answer = """
我将使用 dev-expert 技能来帮你进行前端登录页 Bug 诊断。
<tp>正在打开 **external/dev-expert** 的技能详情。</tp>
<trp>已成功加载 **external/dev-expert** 的技能详情。</trp>

## 六步闭环 Bug 诊断报告
### 根因分析
固定宽度、nowrap 和负边距共同导致移动端溢出。
### 修复方案
使用 max-width、允许标题换行，并去掉错误提示的负向定位。
### 验收标准
320px 下无横向滚动。
### 需要补充的测试用例
覆盖 320px、375px 和长错误文本。
### 复盘总结
移动端布局不能依赖固定宽度。
"""
        trigger_status, _ = detect_skill_trigger("dev-expert", "", answer, "")
        execution_status, evidence = detect_skill_execution("dev-expert", "", answer, "")

        self.assertEqual(trigger_status, "confirmed")
        self.assertEqual(execution_status, "success")
        self.assertIn("根因", evidence)

    def test_self_improving_memory_workflow_is_success(self) -> None:
        answer = """
<tp>正在打开 **external/Self-Improving + Proactive Agent** 的技能详情。</tp>
<trp>已成功加载 **external/Self-Improving + Proactive Agent** 的技能详情。</trp>
已成功向 self-improving/memory.md 写入内容。
已成功向 self-improving/corrections.md 写入内容。
已成功向 self-improving/index.md 写入内容。
已成功向 self-improving/heartbeat-state.md 写入内容。

## 纠错学习完成
### Memory Stats
memory.md: 10 lines
corrections.md: 2 entries
"""
        trigger_status, _ = detect_skill_trigger("Self-Improving + Proactive Agent", "", answer, "")
        execution_status, evidence = detect_skill_execution("Self-Improving + Proactive Agent", "", answer, "")

        self.assertEqual(trigger_status, "confirmed")
        self.assertEqual(execution_status, "success")
        self.assertIn("memory.md", evidence)

    def test_agent_browser_fallback_result_is_degraded_not_failed(self) -> None:
        answer = """
<tp>正在打开 **external/agent-browser** 的技能详情。</tp>
<trp>已成功加载 **external/agent-browser** 的技能详情。</trp>
<trp>技能 **agent-browser** 的预定义脚本未能运行完成。</trp>
改用页面读取完成检查。
页面标题：Example Domain
主要内容：This domain is for use in documentation examples.
"""
        trigger_status, _ = detect_skill_trigger("agent-browser", "", answer, "")
        execution_status, evidence = detect_skill_execution("agent-browser", "", answer, "")

        self.assertEqual(trigger_status, "confirmed")
        self.assertEqual(execution_status, "degraded")
        self.assertIn("fallback_webpage_inspection", evidence)

    def test_text_task_result_without_tool_marker_can_be_success(self) -> None:
        answer = (
            "根因分析：固定宽度和 nowrap 导致移动端溢出，错误提示使用负向定位导致遮挡。"
            "修复方案：使用 max-width、允许换行、移除负边距。验收标准：320px 和 375px 下无横向滚动。"
            "复盘总结：移动端布局要优先使用弹性约束。"
        )

        self.assertTrue(has_task_result(answer))
        execution_status, evidence = detect_skill_execution("generic-advisor", "", answer, "")

        self.assertEqual(execution_status, "success")
        self.assertEqual(evidence, ["task_result_without_tool_success_marker"])

    def test_batch_demo_case_parser_accepts_wrapped_json(self) -> None:
        text = '```json\n{"cases":[{"name":"weather","id":"core-flow","prompt":"请使用 weather Skill 查询北京今天的天气，并给出穿衣建议。"}]}\n```'

        cases = parse_batch_demo_case_response(text)

        self.assertEqual(cases[0]["name"], "weather")

    def test_demo_case_validation_requires_specific_skill_name(self) -> None:
        self.assertFalse(valid_demo_case("weather", {"prompt": "请使用这个 Skill 完成一个核心示例任务。"}))
        self.assertFalse(valid_demo_case("weather", {"prompt": "请帮我查询北京今天的天气并给出建议。"}))
        self.assertTrue(valid_demo_case("weather", {"prompt": "请使用 weather Skill 查询北京今天的天气，并给出穿衣建议。"}))

    def test_demo_case_validation_rejects_overlong_generated_prompt(self) -> None:
        prompt = "请使用 weather Skill " + "查询北京天气并给出穿衣、通勤、运动、空气质量、降雨风险和周末安排建议。" * 10

        self.assertFalse(valid_demo_case("weather", {"prompt": prompt}))

    def test_demo_case_validation_rejects_local_environment_dependencies(self) -> None:
        self.assertTrue(has_local_dependency_marker("请使用 agent-browser Skill 打开 http://localhost:3000/login 并截图。"))
        self.assertTrue(has_local_dependency_marker("请使用 dev-expert Skill 审查当前项目中 src/auth.ts 的登录限流逻辑。"))
        self.assertTrue(
            valid_demo_case(
                "agent-browser",
                {"prompt": "请使用 agent-browser Skill 打开 https://example.com，提取页面标题和正文第一句话。"},
            )
        )
        self.assertFalse(
            valid_demo_case(
                "dev-expert",
                {"prompt": "请使用 dev-expert Skill 审查当前项目中 src/auth.ts 的登录限流逻辑。"},
            )
        )

    def test_static_batch_generation_returns_all_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cases = generate_test_cases_batch(
                [
                    {
                        "key": "0",
                        "name": "weather",
                        "skill_type": "data",
                        "source_path": Path(tmp),
                    }
                ],
                demo_case_generator="static",
                batch_size=5,
            )

        self.assertIn("0", cases)
        self.assertEqual(cases["0"][0]["source"], "static_type_template")

    def test_codex_batch_generation_uses_json_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp)
            (skill_dir / "SKILL.md").write_text("查询城市天气并给出生活建议。", encoding="utf-8")
            with patch("skill_auto.runner.run_codex_case_prompt") as mock_run:
                mock_run.return_value = (
                    '{"cases":[{"name":"weather","id":"core-flow",'
                    '"prompt":"请使用 weather Skill 查询北京今天的天气，并给出穿衣和通勤建议。"}]}'
                )

                cases = generate_test_cases_batch(
                    [{"key": "0", "name": "weather", "skill_type": "data", "source_path": skill_dir}],
                    demo_case_generator="codex",
                    batch_size=5,
                    base_url="http://127.0.0.1:8090",
                )

        self.assertEqual(cases["0"][0]["source"], "codex_batch_generated")
        self.assertEqual(cases["0"][0]["generator_model"], "codex")

    def test_codex_batch_generation_repairs_missing_case_one_by_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp)
            (skill_dir / "SKILL.md").write_text("查询城市天气并给出生活建议。", encoding="utf-8")
            with patch("skill_auto.runner.run_codex_case_prompt") as mock_run:
                mock_run.side_effect = [
                    '{"cases":[]}',
                    '{"id":"core-flow","prompt":"请使用 weather Skill 查询上海明天的天气，并给出出行准备建议。"}',
                ]

                cases = generate_test_cases_batch(
                    [{"key": "0", "name": "weather", "skill_type": "data", "source_path": skill_dir}],
                    demo_case_generator="codex",
                    batch_size=5,
                    base_url="http://127.0.0.1:8090",
                )

        self.assertEqual(cases["0"][0]["source"], "codex_single_repair")
        self.assertEqual(cases["0"][0]["generator_model"], "codex")

    def test_model_case_normalization_requires_prompt(self) -> None:
        self.assertIsNone(normalize_model_demo_case({"id": "core-flow"}))
        self.assertEqual(
            normalize_model_demo_case({"prompt": "请使用 weather Skill 查询北京天气。"})["id"],
            "core-flow",
        )

    def test_rate_limit_observation_detection(self) -> None:
        self.assertTrue(
            is_rate_limited_observation(
                {
                    "failure_category": "model_rate_limited",
                    "response_excerpt": "模型调用失败，请求过于频繁。",
                }
            )
        )

    def test_semantic_output_normalization(self) -> None:
        normalized = normalize_semantic_output(
            {
                "trigger_status": "confirmed",
                "skill_execution_status": "blocked",
                "task_completed": False,
                "used_skill": True,
                "core_requirement_met": False,
                "confidence": "0.9",
                "failure_category": "network_failed",
                "evidence": ["所有天气接口均未能加载"],
                "reason": "未返回真实天气数据",
            }
        )

        self.assertEqual(normalized["skill_execution_status"], "blocked")
        self.assertEqual(normalized["confidence"], 0.9)

    def test_semantic_result_overrides_rule_observation(self) -> None:
        observation = {
            "status": "pass",
            "skill_trigger_status": "confirmed",
            "skill_execution_status": "success",
            "skill_trigger_evidence": ["external/weather"],
            "skill_execution_evidence": ["天气", "温度"],
            "failure_category": None,
        }
        semantic = normalize_semantic_output(
            {
                "trigger_status": "confirmed",
                "skill_execution_status": "blocked",
                "task_completed": False,
                "used_skill": True,
                "core_requirement_met": False,
                "confidence": 0.95,
                "failure_category": "network_failed",
                "evidence": ["wttr.in 和 Open-Meteo 均未能加载"],
                "reason": "没有获取到真实天气数据",
                "suggested_fix": "检查网络或数据源 fallback。",
            }
        )
        semantic["semantic_eval_status"] = "succeeded"

        apply_semantic_result(observation, semantic)

        self.assertEqual(observation["status"], "fail")
        self.assertEqual(observation["rule_skill_execution_status"], "success")
        self.assertEqual(observation["skill_execution_status"], "blocked")
        self.assertEqual(observation["failure_category"], "network_failed")

    def test_semantic_summary_is_demo_only(self) -> None:
        self.assertEqual(summarize_semantic_results([], "smoke")["semantic_eval_status"], "not_tested")
        self.assertEqual(
            summarize_semantic_results(
                [{"semantic_evaluation": {"semantic_eval_status": "succeeded"}}],
                "demo",
            )["semantic_eval_status"],
            "succeeded",
        )

    def test_redacts_secret_values_from_observation(self) -> None:
        observation = {
            "prompt": "REDFOX_API_KEY=redacted_secret_value\n请使用 skill",
            "semantic_evaluation": {"reason": "使用 redacted_secret_value 失败"},
            "skill_execution_evidence": ["redacted_secret_value"],
        }

        redacted = redact_observation_secrets(observation, ["redacted_secret_value"])

        self.assertEqual(redacted["prompt"], "REDFOX_API_KEY=***REDACTED***\n请使用 skill")
        self.assertEqual(redacted["semantic_evaluation"]["reason"], "使用 ***REDACTED*** 失败")
        self.assertEqual(redacted["skill_execution_evidence"], ["***REDACTED***"])

    def test_preferred_observation_failure_ignores_env_marker_when_specific_failure_exists(self) -> None:
        detail = preferred_observation_failure(
            [
                {"failure_category": "missing_env", "failure_technical_reason": "REDFOX_API_KEY marker detected"},
                {"failure_category": "network_failed", "failure_technical_reason": "image url access failed"},
            ]
        )

        self.assertEqual(detail["failure_category"], "network_failed")

    def test_extract_output_artifacts_detects_html_and_static_files(self) -> None:
        text = "报告已生成并保存：[爆款封面分析报告_职场成长.html](file_id:8227ce4b-91e8-45ed-b8e9-b8cf0b9018d1)，模板 references/report_template.html，图片 /static-files/img.png"

        artifacts = extract_output_artifacts(text)

        self.assertIn("爆款封面分析报告_职场成长.html", artifacts)
        self.assertIn("file_id:8227ce4b-91e8-45ed-b8e9-b8cf0b9018d1", artifacts)
        self.assertIn("/static-files/img.png", artifacts)
        self.assertNotIn("references/report_template.html", artifacts)

    def test_aggregate_output_artifacts_deduplicates_attempts(self) -> None:
        artifacts = aggregate_output_artifacts(
            [
                {"output_artifacts": ["爆款封面分析报告_职场成长.html"]},
                {"output_artifacts": ["爆款封面分析报告_职场成长.html", "/static-files/img.png"]},
            ]
        )

        self.assertEqual(artifacts, ["爆款封面分析报告_职场成长.html", "/static-files/img.png"])

    def test_history_answer_complete_detects_saved_html(self) -> None:
        self.assertTrue(is_history_answer_complete("工具 **save_chat_artifact** 已调用完成。报告已生成并保存。"))
        self.assertTrue(is_history_answer_complete("已成功向 **./爆款封面分析报告_职场成长.html** 写入内容。"))
        self.assertFalse(is_history_answer_complete("正在读取文件 tool_spills/run_script.txt。"))

    def test_parse_conversation_status_is_generating(self) -> None:
        self.assertTrue(parse_is_generating('{"is_generating": true}'))
        self.assertFalse(parse_is_generating('{"data": {"is_generating": false}}'))
        self.assertIsNone(parse_is_generating('{"status": "completed"}'))

    def test_latest_history_item_and_terminal_status(self) -> None:
        body = (
            '{"history":['
            '{"result":"new","run_status":"completed","run_terminal":{"status":"completed","reason":"normal","partial_output":true}},'
            '{"result":"old","run_status":"completed"}'
            ']}'
        )

        item = latest_history_item(body)

        self.assertEqual(item["result"], "new")
        self.assertTrue(is_terminal_history_item(item))
        self.assertEqual(latest_history_run_status(item), "completed")
        self.assertEqual(extract_history_answer(body), "new")

    def test_history_terminal_can_fall_back_to_run_status(self) -> None:
        item = {"result": "answer", "run_status": "failed"}

        self.assertTrue(is_terminal_history_item(item))
        self.assertEqual(latest_history_run_status(item), "failed")


if __name__ == "__main__":
    unittest.main()
