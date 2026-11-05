// Public Pi SDK and extension loading; inputs only. Python owns expected values.
import { readFileSync, mkdirSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
const input = JSON.parse(readFileSync(0, 'utf8'));
async function publicImport(name, entry = '.') {
    const directory = join(resolve(input.runtime), 'node_modules', name);
    const manifest = JSON.parse(readFileSync(join(directory, 'package.json'), 'utf8'));
    const target = manifest.exports[entry];
    return [await import(pathToFileURL(join(directory, typeof target === 'string' ? target : target.import))), manifest];
}
const [sdk, manifest] = await publicImport('@earendil-works/pi-coding-agent');
const [ai] = await publicImport('@earendil-works/pi-ai', './compat');
const { createAgentSession, ModelRuntime, DefaultResourceLoader, SessionManager, SettingsManager } = sdk;
mkdirSync(input.directory, { recursive: true });
const faux = ai.registerFauxProvider({ tokensPerSecond: 100000,
    ...(input.modelId ? { models: [{ id: input.modelId }] } : {}) });
const model = faux.getModel();
const modelRuntime = await ModelRuntime.create({ authPath: join(input.directory, 'auth.json'),
    modelsPath: join(input.directory, 'models.json'), allowModelNetwork: false });
modelRuntime.registerProvider(model.provider, { baseUrl: model.baseUrl, api: model.api, models: [model] });
await modelRuntime.setRuntimeApiKey(model.provider, 'unused-scripted-fixture');
faux.setResponses([...input.cases.map(c => ai.fauxAssistantMessage(ai.fauxToolCall(c.tool, c.arguments, { id: c.id }),
    { stopReason: 'toolUse' })), ai.fauxAssistantMessage('Deterministic probe finished')]);
const settings = SettingsManager.inMemory({ compaction: { enabled: false }, retry: { enabled: false } });
const resources = new DefaultResourceLoader({ cwd: input.directory, agentDir: input.directory, settingsManager: settings,
    additionalExtensionPaths: [input.extension], noSkills: true, noPromptTemplates: true, noThemes: true, noContextFiles: true });
await resources.reload();
const { session, extensionsResult } = await createAgentSession({ cwd: input.directory, agentDir: input.directory,
    model, modelRuntime, settingsManager: settings, resourceLoader: resources, sessionManager: SessionManager.inMemory(input.directory) });
const results = {};
try {
    if (extensionsResult.errors?.length) throw new Error(JSON.stringify(extensionsResult.errors));
    session.subscribe(event => {
        if (event.type === 'tool_execution_end') {
            const result = event.result;
            results[event.toolCallId] = { success: !event.isError,
                content: (result.content ?? []).filter(x => x.type === 'text').map(x => x.text).join('\n'),
                error: event.isError ? { code: 'plugin_error' } : null };
        }
    });
    await session.prompt('Execute the supplied deterministic reimbursement-rule probes.');
} finally {
    session.dispose();
    faux.unregister();
}
console.log(JSON.stringify({ results, scripted_requests: faux.state.callCount, model_api_calls: 0,
    sdk_version: manifest.version }));
