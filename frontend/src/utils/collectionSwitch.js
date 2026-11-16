/**
 * Which tab to show after the collection changes from the header picker or
 * the command palette.
 *
 * Every tab stays where it is: Research, Reports, Connect and Settings all
 * follow the current collection, and being thrown back to Ask lost the place
 * someone was working in. The one exception is the collections overview,
 * where picking a collection means "open it", so that lands on Ask (or Find
 * when Ask is turned off).
 */
export function tabAfterCollectionSwitch(tab, { chatEnabled = true } = {}) {
  if (tab === 'collections') return chatEnabled ? 'chat' : 'search'
  return tab
}
