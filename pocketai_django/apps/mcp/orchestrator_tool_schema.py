from __future__ import annotations

from typing import Mapping, Sequence


class McpToolSchemaMixin:

    @staticmethod
    def _is_knowledge_tool(name: str) -> bool:
        return name in {"search_knowledge", "read_knowledge"}

    @staticmethod
    def _tool_schema_name(tool_def: Mapping[str, object]) -> str | None:
        if not isinstance(tool_def, Mapping):
            return None
        func = tool_def.get("function")
        if isinstance(func, Mapping):
            name = func.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
        return None

    def _tool_parameters(self, tool_name: str) -> Mapping[str, object] | None:
        if not tool_name:
            return None
        for tool_def in self.tool_definitions:
            name = self._tool_schema_name(tool_def)
            if name != tool_name:
                continue
            func = tool_def.get("function")
            if isinstance(func, Mapping):
                params = func.get("parameters")
                if isinstance(params, Mapping):
                    return params
            return None
        return None

    @staticmethod
    def _is_missing_value(value: object) -> bool:
        if value is None:
            return True
        if isinstance(value, str):
            return not value.strip()
        if isinstance(value, (list, tuple, set)):
            return len(value) == 0
        if isinstance(value, dict):
            return len(value) == 0
        return False

    @staticmethod
    def _mcp_setup_fields_for_connection(connection: object) -> dict[str, str]:
        creds = getattr(connection, "credentials", None)
        if not isinstance(creds, Mapping):
            return {}
        setup_fields = creds.get("setup_fields")
        if not isinstance(setup_fields, Mapping):
            return {}
        cleaned: dict[str, str] = {}
        for key, value in setup_fields.items():
            name = str(key or "").strip()
            if not name or not isinstance(value, str):
                continue
            val = value.strip()
            if not val:
                continue
            cleaned[name] = val
        return cleaned


    def _apply_mcp_setup_defaults(
        self,
        arguments: Mapping[str, object],
        *,
        connection: object,
        input_schema: Mapping[str, object] | None,
    ) -> tuple[dict[str, object], list[str]]:
        """
        Apply per-connection marketplace setup fields as default tool arguments.

        Defaults are applied only when:
        - the remote tool schema declares the key in properties, and
        - the argument is missing/empty in the tool call.

        Returns (effective_arguments, defaults_applied_keys).
        """
        merged = dict(arguments or {})
        defaults = self._mcp_setup_fields_for_connection(connection)
        if not defaults:
            return merged, []
        props = input_schema.get("properties") if isinstance(input_schema, Mapping) else None
        if not isinstance(props, Mapping) or not props:
            return merged, []
        applied: list[str] = []
        for key, value in defaults.items():
            if key not in props:
                continue
            if key in merged and not self._is_missing_value(merged.get(key)):
                continue
            merged[key] = value
            applied.append(key)
        return merged, applied

    def _missing_required_fields(self, tool_name: str, arguments: Mapping[str, object]) -> list[str]:
        params = self._tool_parameters(tool_name)
        if not params:
            return []
        required = params.get("required")
        if not isinstance(required, list) or not required:
            return []
        missing: list[str] = []
        for field in required:
            if not isinstance(field, str):
                continue
            if tool_name == "mcp_call_tool" and field == "arguments":
                # Gateway tool always requires an arguments object, but it may be empty
                # for remote tools with no required args.
                if field in arguments and isinstance(arguments.get(field), Mapping):
                    continue
            if field not in arguments or self._is_missing_value(arguments.get(field)):
                missing.append(field)
        return missing

    def _validate_gateway_tool_arguments(
        self,
        arguments: Mapping[str, object],
        input_schema: Mapping[str, object] | None,
    ) -> tuple[list[str], list[dict[str, str]]]:
        if not input_schema:
            return [], []

        required = input_schema.get("required")
        required_fields = [str(field).strip() for field in required if isinstance(field, str) and field.strip()] if isinstance(required, list) else []
        missing_fields = [
            field
            for field in required_fields
            if field not in arguments or self._is_missing_value(arguments.get(field))
        ]

        properties = input_schema.get("properties")
        props = properties if isinstance(properties, Mapping) else {}
        type_errors: list[dict[str, str]] = []
        for key, value in arguments.items():
            schema_node = props.get(key)
            if not isinstance(schema_node, Mapping):
                continue
            expected = schema_node.get("type")
            expected_types: list[str] = []
            if isinstance(expected, str) and expected.strip():
                expected_types = [expected.strip()]
            elif isinstance(expected, list):
                expected_types = [str(entry).strip() for entry in expected if str(entry).strip()]

            if not expected_types:
                continue

            allows_null = "null" in expected_types
            if value is None and allows_null:
                continue

            expected_non_null = [t for t in expected_types if t != "null"] or expected_types
            matches_any = any(self._gateway_value_matches_json_type(value, t) for t in expected_non_null)
            if matches_any:
                continue
            type_errors.append(
                {
                    "field": str(key),
                    "expected": "|".join(expected_non_null[:4]),
                    "received": type(value).__name__,
                }
            )
            if len(type_errors) >= 12:
                break

        return missing_fields[:12], type_errors

    @staticmethod
    def _gateway_value_matches_json_type(value: object, expected_type: str) -> bool:
        normalized = str(expected_type or "").strip().lower()
        if not normalized or normalized == "any":
            return True
        if normalized == "string":
            return isinstance(value, str)
        if normalized == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if normalized == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool)
        if normalized == "boolean":
            return isinstance(value, bool)
        if normalized == "object":
            return isinstance(value, Mapping)
        if normalized == "array":
            return isinstance(value, (list, tuple))
        return True

    @staticmethod
    def _missing_required_payload(tool_name: str, missing_fields: Sequence[str]) -> Mapping[str, object]:
        field_list = [str(field) for field in missing_fields if str(field).strip()]
        summary = ", ".join(field_list) if field_list else "required fields"
        hint = (
            "Ask the visitor to provide the missing required fields before retrying this tool call. "
            f"Missing: {summary}."
        )
        return {
            "tool": tool_name,
            "status": "constraint_error",
            "error": f"Missing required tool fields: {summary}",
            "error_code": "missing_required_fields",
            "missing_fields": field_list,
            "hint": hint,
            "llm_hint": hint,
            "snippets": [],
        }

    def _exclude_tool_schemas(self, excluded_names: set[str]) -> list[Mapping[str, object]]:
        if not excluded_names:
            return list(self.tool_definitions)
        filtered: list[Mapping[str, object]] = []
        for tool_def in self.tool_definitions:
            name = self._tool_schema_name(tool_def)
            if name and name in excluded_names:
                continue
            filtered.append(tool_def)
        return filtered

    def _include_tool_schemas(self, included_names: set[str]) -> list[Mapping[str, object]]:
        if not included_names:
            return list(self.tool_definitions)
        filtered: list[Mapping[str, object]] = []
        for tool_def in self.tool_definitions:
            name = self._tool_schema_name(tool_def)
            if not name or name not in included_names:
                continue
            filtered.append(tool_def)
        return filtered or list(self.tool_definitions)

