from forge.agents import AgentDefinition


def activate(ctx, config):
    async def coordinate(args):
        summaries = []
        for event in args['events']:
            result = await ctx.run_child('Summarize this local event: ' + event,
                                        AgentDefinition.core('event-worker'), role='worker')
            if result.status != 'completed':
                raise RuntimeError('Child did not complete')
            summaries.append(result.text)
        return {'summaries': summaries}
    ctx.register_tool('coordinate', 'Summarize local events through managed child sessions', {
        'type': 'object', 'properties': {'events': {'type': 'array', 'items': {'type': 'string'}, 'maxItems': 5}},
        'required': ['events'], 'additionalProperties': False}, coordinate)
