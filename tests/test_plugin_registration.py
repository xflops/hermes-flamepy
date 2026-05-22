from hermes_flamepy import register


class FakeContext:
    def __init__(self):
        self.tools = []

    def register_tool(self, **kwargs):
        self.tools.append(kwargs)


def test_registers_flmexec_in_hermes_flamepy_toolset():
    ctx = FakeContext()

    register(ctx)

    assert len(ctx.tools) == 1
    tool = ctx.tools[0]
    assert tool["name"] == "flmexec"
    assert tool["toolset"] == "hermes-flamepy"
    assert tool["schema"]["name"] == "flmexec"
