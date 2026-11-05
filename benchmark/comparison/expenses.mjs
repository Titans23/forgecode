// Same public business contract as examples/agents/expenses, via Pi's extension API.
export default function activate(pi) {
    const limit = 100;
    pi.registerTool({ name: 'summarize_expenses', label: 'Expenses', description: 'Apply reimbursement rules to a CSV table',
        parameters: { type: 'object', properties: { csv: { type: 'string' } }, required: ['csv'], additionalProperties: false },
        async execute(_id, args) {
            const lines = args.csv.trim().split(/\r?\n/);
            const headers = lines.shift().split(',');
            let cents = 0, rejected = 0;
            for (const line of lines) {
                const fields = line.split(',');
                const amount = Number(fields[headers.indexOf('amount')]);
                const category = fields[headers.indexOf('category')];
                if (!Number.isFinite(amount) || amount < 0 || amount > limit || category === 'alcohol') rejected++;
                else cents += Math.round(amount * 100);
            }
            return { content: [{ type: 'text', text: JSON.stringify({ total: (cents / 100).toFixed(2), rejected }) }], details: {} };
        } });
}
