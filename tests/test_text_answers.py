import json
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import ifcopenshell
from stoa import pipeline, verda
from stoa.answer import Answer, parse_llm_answer
from stoa.inspect_file import FileInfo
from stoa.tools import ifc_tools as ifc


class IfcTextTests(unittest.TestCase):
    def setUp(self):
        self.file = ifcopenshell.file(schema="IFC4")

    def element(self, name, width=1):
        return self.file.create_entity("IfcDoor", GlobalId=ifcopenshell.guid.new(),
                                       Name=name, OverallWidth=width)

    def table(self, elements, props=None, **kwargs):
        with ExitStack() as stack:
            stack.enter_context(patch.object(ifc, "_elements", return_value=elements))
            stack.enter_context(patch.object(ifc, "_scales", return_value={"length": 1}))
            stack.enter_context(patch.object(ifc, "_quantity", return_value=(None, "missing")))
            if props is not None:
                stack.enter_context(patch.object(ifc, "_common_props", side_effect=lambda e: props[e.GlobalId]))
            return ifc.ifc_list_elements("fixture", "IfcDoor", **kwargs)

    def test_guid_lookup_is_exact_and_absent_guid_is_not_guessed(self):
        small, large = self.element("Small:123", .8), self.element("Large:124", 1.25)
        result = self.table([small, large], global_id=large.GlobalId)
        self.assertEqual(result.value["elements"], 1)
        self.assertEqual(result.value["rows"][0]["OverallWidth"], 1.25)
        self.assertEqual(result.evidence[0].ref, large.GlobalId)
        with self.assertRaises(ifc.ToolError):
            self.table([small, large], global_id="missing")

    def test_explicit_interior_filter_excludes_unknown_and_exterior(self):
        elements = [self.element(str(i)) for i in range(3)]
        props = {e.GlobalId: p for e, p in zip(elements,
                  ({"IsExternal": False}, {"IsExternal": True}, {}))}
        result = self.table(elements, props, is_external=False).value
        self.assertEqual(result["elements"], 1)
        self.assertEqual(result["unknown_external_classification_before_filter"], 1)

    def test_summary_uses_all_elements_even_when_detail_rows_are_limited(self):
        elements = [self.element(f"Door type {i}:1234", i+1) for i in range(121)]
        result = self.table(elements).value
        self.assertEqual(len(result["rows"]), 120)
        self.assertEqual(result["omitted_groups"], 1)
        self.assertEqual(sum(g["count"] for g in result["by_name"]), 121)
        self.assertEqual(result["size_statistics"]["OverallWidth"]["instance_weighted_mean"], 61)
        self.assertEqual(result["size_statistics"]["OverallWidth"]["max"], 121)

    def test_material_layers_preserve_repeated_layers_and_convert_units(self):
        mat = self.file.create_entity("IfcMaterial", Name="Membrane")
        layers = [self.file.create_entity("IfcMaterialLayer", Material=mat, LayerThickness=t) for t in (1, 2)]
        material = self.file.create_entity("IfcMaterialLayerSet", MaterialLayers=layers)
        with patch.object(ifc.ue, "get_material", return_value=material), \
             patch.object(ifc, "_scales", return_value={"length": .001}):
            result = ifc._material_layers("fixture", None)
        self.assertEqual([r["thickness_mm"] for r in result], [1, 2])
        self.assertEqual(len(result), 2)

    def test_exporter_wall_layer_classification_has_provenance_and_standard_wins(self):
        with patch.object(ifc, "_common_props", return_value={}), \
             patch.object(ifc.ue, "get_psets", return_value={"ArchiCADProperties": {"Ebene": "Außenwände"}}):
            value, source = ifc._external_classification(None)
            self.assertTrue(value)
            self.assertIn("ArchiCADProperties", source)
        with patch.object(ifc, "_common_props", return_value={"IsExternal": False}):
            self.assertEqual(ifc._external_classification(None), (False, "Pset_*Common.IsExternal"))


class ToolCall:
    def __init__(self, identifier, args):
        self.id = identifier
        self.function = SimpleNamespace(name="ifc_count", arguments=json.dumps(args))

    def model_dump(self, **kwargs):
        return {"id": self.id, "type": "function", "function": vars(self.function)}


class CompositionTests(unittest.TestCase):
    def run_pipeline(self, calls, error=None, content=None):
        tool = pipeline.Tool("ifc_count", "test", {"ifc_class": pipeline._s("class")},
                             "ifc", lambda path, ifc_class: Answer(14))
        first = SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop",
            message=SimpleNamespace(tool_calls=calls, content=None))])
        last = SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop",
            message=SimpleNamespace(tool_calls=None,
                content=content or '{"answer":"14 doors; assuming 2 knobs each, approximately 28 knobs."}'))])
        with patch.object(pipeline, "inspect_file", return_value=FileInfo(Path("a.ifc"), "ifc", ifc=Path("a.ifc"))), \
             patch.object(pipeline, "tools_for", return_value=[tool]), \
             patch.object(pipeline, "_describe", return_value="fixture"), \
             patch.object(verda, "chat", side_effect=[first, error or last]) as chat, \
             patch.object(verda, "usage_of", return_value={"cost_usd": .01}):
            result = pipeline.answer("a.ifc", "How many doorknobs?", router="hybrid")
        return result, chat

    def test_partial_tool_result_is_composed_not_returned_as_final_number(self):
        result, chat = self.run_pipeline([ToolCall("1", {"ifc_class": "IfcDoor", "answer_directly": False})])
        self.assertIn("28 knobs", result.answer.value)
        self.assertEqual(chat.call_count, 2)
        self.assertEqual(chat.call_args.kwargs["max_tokens"], 4096)

    def test_text_answer_drops_measurement_unit_before_composition(self):
        text = "图纸缺少房价信息，无法估价。已测得面积为 227.46 m²。"
        for unit in ("m2", "count"):
            with self.subTest(unit=unit):
                result, _ = self.run_pipeline(
                    [ToolCall("1", {"ifc_class": "IfcDoor", "answer_directly": False})],
                    content=json.dumps({"answer": text, "unit": unit, "evidence": ["e1"]}),
                )
                self.assertEqual(result.to_json()["answer"]["value"], text)
                self.assertIsNone(result.to_json()["answer"]["unit"])

    def test_multiple_tools_strip_direct_flag_even_after_first_false(self):
        result, chat = self.run_pipeline([
            ToolCall("1", {"ifc_class": "IfcDoor", "answer_directly": False}),
            ToolCall("2", {"ifc_class": "IfcWindow", "answer_directly": True})])
        outputs = [m["content"] for m in chat.call_args.args[1] if m["role"] == "tool"]
        self.assertFalse(any("ERROR" in output for output in outputs))
        self.assertEqual(len(outputs), 2)

    def test_complete_numeric_result_retains_single_call(self):
        result, chat = self.run_pipeline([ToolCall("1", {"ifc_class": "IfcDoor", "answer_directly": True})])
        self.assertEqual(result.answer.value, 14)
        self.assertEqual(chat.call_count, 1)

    def test_later_api_failure_retains_previous_successful_usage(self):
        result, _ = self.run_pipeline(
            [ToolCall("1", {"ifc_class": "IfcDoor", "answer_directly": False})],
            error=RuntimeError("API unavailable"))
        self.assertIn("API unavailable", result.error)
        self.assertEqual(result.llm_calls, 1)
        self.assertEqual(result.cost_usd, .01)


class AnswerParsingTests(unittest.TestCase):
    def test_numeric_answers_keep_units(self):
        for value, expected in ((227.46, 227.46), (0, 0), ("227.46", 227.46), ("1,234.50", 1234.5)):
            with self.subTest(value=value):
                parsed = parse_llm_answer(json.dumps({"answer": value, "unit": "m2"}))
                self.assertEqual(parsed["answer"], expected)
                self.assertEqual(parsed["unit"], "m2")

    def test_non_numeric_answers_drop_units_and_preserve_content(self):
        for value in ("无法估价", "", None, False, {"rooms": 4}, ["Bad", "Küche"]):
            with self.subTest(value=value):
                parsed = parse_llm_answer(json.dumps({"answer": value, "unit": "m2"}))
                self.assertEqual(parsed["answer"], value)
                self.assertIsNone(parsed["unit"])


if __name__ == "__main__":
    unittest.main()
