// The first task the Connect tab suggests handing to a newly connected agent.
//
// A plain question would show nothing the built-in chat cannot already do.
// With the sample documents loaded, the task is an audit that needs the
// agent to combine tools: rules from the policy and its amendment (passages),
// every checkout from the register (a table), and a verdict per row that
// depends on dates, the field-team exception and approved extensions. The
// records have a definite answer, including one checkout still out past its
// due date, so a person can tell whether the agent got it right.
//
// The server offers the same task as the MCP prompt `try_clio`
// (services/mcp_server.py: prompt_try_clio), with fuller tool instructions.

/**
 * @param {object} opts
 * @param {object|null} opts.sampleCollection  the sample collection, when it exists
 * @param {object|null} opts.collection        the collection the recipes point at
 * @param {boolean} opts.codingClient          the selected client works in files
 * @returns {{title: string, text: string, why: string}}
 */
export function firstTaskPrompt({ sampleCollection, collection, codingClient }) {
  if (sampleCollection) {
    const text =
      `Using the clio tools, audit the equipment register in the "${sampleCollection.name}" collection ` +
      'against EQ-17 and its amendment. For each checkout, say which rule applied and whether it was ' +
      'on time, late, covered by an approved extension, or is still out past its due date. Cite the ' +
      "document behind each rule, and tell me what the records can't settle." +
      (codingClient ? ' Write the result to audit.md.' : '')
    return {
      title: 'First task: audit the sample register',
      text,
      why:
        'The agent has to pull the rules from two documents, every row from the register, and apply ' +
        'the right rule to each one. There is a definite answer: four checkouts came back late with no ' +
        'extension, and one is still out.',
    }
  }
  const name = collection?.name || 'Default'
  return {
    title: 'First task',
    text:
      `Using the clio tools, look through my "${name}" collection and tell me what it covers. Then ` +
      'answer one question it should answer well, citing the files you used and saying what they ' +
      "don't establish." +
      (codingClient ? ' Save your notes to clio-notes.md.' : ''),
    why: '',
  }
}
