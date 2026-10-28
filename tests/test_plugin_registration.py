from hermes_flamepy import register


class FakeContext:
    def __init__(self):
        self.tools = []

    def register_tool(self, **kwargs):
        self.tools.append(kwargs)


def test_registers_tools_in_flamepy_toolset():
    ctx = FakeContext()

    register(ctx)

    assert [t["name"] for t in ctx.tools] == ["sandbox", "session"]
    for tool in ctx.tools:
        assert tool["toolset"] == "flamepy"
        assert tool["is_async"] is True
        assert tool["schema"]["name"] == tool["name"]
