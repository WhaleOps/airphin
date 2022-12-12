import copy
from typing import Optional, Sequence, Union

import libcst as cst
import libcst.matchers as m
from libcst import Arg, BaseExpression, FlattenSentinel, RemovalSentinel

from airphin.core.rules.config import CallConfig, Config


class OpTransformer(cst.CSTTransformer):
    """CST Transformer for airflow operators.

    TODO Need to skip inner call like DAG(date_time=datetime.datetime.now().strftime("%Y-%m-%d"))

    :param qualified_name: qualified name of operator
    """

    def __init__(self, config: Config, qualified_name: Optional[str] = None):
        super().__init__()
        self._config: Config = config
        self.qualified_name = qualified_name
        assert self.qualified_name is not None
        self.visit_name = False
        self.converted_param = set()

    @property
    def config(self) -> CallConfig:
        return self._config.calls.get(self.qualified_name)

    def matcher_op_name(self, node: cst.Name) -> bool:
        if self.visit_name is False and node.value == self.config.src_short:
            self.visit_name = True
            return True
        return False

    def matcher_param_name(self, node: cst.Arg) -> bool:
        convert_names = self.config.param.keys()
        return m.matches(
            node,
            m.Arg(keyword=m.Name(m.MatchIfTrue(lambda name: name in convert_names))),
        )

    def leave_ImportFrom(
        self, original_node: cst.ImportFrom, updated_node: cst.ImportFrom
    ) -> Union[
        cst.BaseSmallStatement, FlattenSentinel[cst.BaseSmallStatement], RemovalSentinel
    ]:
        if self.matcher_import_name(original_node):
            dest_module = self.config.module
            return updated_node.with_changes(module=cst.Name(value=dest_module))
        return updated_node

    def leave_Name(
        self, original_node: cst.Name, updated_node: cst.Name
    ) -> "BaseExpression":
        if self.matcher_op_name(original_node):
            dest_name = self.config.short
            return updated_node.with_changes(value=dest_name)
        return updated_node

    def leave_Arg(
        self, original_node: cst.Arg, updated_node: cst.Arg
    ) -> Union[Arg, FlattenSentinel[cst.Arg], RemovalSentinel]:
        if self.matcher_param_name(original_node):
            original_keyword = original_node.keyword.value
            dest_keyword = self.config.param.get(original_keyword)

            self.converted_param.add(dest_keyword)
            # also change to default value when have ``default`` node
            if dest_keyword is self.config.default:
                default_value: str = self.config.default.get(dest_keyword)
                return updated_node.with_changes(
                    keyword=cst.Name(value=dest_keyword),
                    value=cst.SimpleString(value=default_value),
                )

            return updated_node.with_changes(keyword=cst.Name(value=dest_keyword))
        return updated_node

    def _handle_missing_default(self, nodes: Sequence[cst.Arg]) -> Sequence[cst.Arg]:
        miss_default = self.config.default.keys() - self.converted_param
        if not miss_default:
            return nodes

        mutable = list(nodes)
        one_of = copy.deepcopy(mutable[-1])
        for miss in miss_default:
            value = self.config.default.get(miss)

            mutable.append(
                one_of.with_changes(
                    value=cst.SimpleString(value=f'"{value}"'),
                    keyword=cst.Name(value=miss),
                )
            )
        return mutable

    def leave_Call(
        self, original_node: cst.Call, updated_node: cst.Call
    ) -> BaseExpression:
        if not self.config.default:
            return updated_node

        return updated_node.with_changes(
            args=self._handle_missing_default(updated_node.args)
        )
