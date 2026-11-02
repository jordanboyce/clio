import http from './http'

// A small fictional record set someone can try Clio on before they have
// their own documents: a policy, an amendment that changes it for one group,
// an older informal note that disagrees with the amendment, and a register
// of checkouts. Together they show citations, conflict handling, the
// "not in the sources" answer, and table questions answered with SQL.
//
// The files are static assets (frontend/public/samples) and go through the
// ordinary /documents/upload endpoint, so the storage cap, the
// acceptable-use gate, attribution and the audit trail all apply exactly as
// they do to any other upload. There is deliberately no server-side
// "sample" ingest path.
export const SAMPLE_FILES = [
  { name: '01-equipment-policy.md', type: 'text/markdown' },
  { name: '02-equipment-amendment.md', type: 'text/markdown' },
  { name: '03-equipment-desk-note.md', type: 'text/markdown' },
  { name: 'equipment-register.csv', type: 'text/csv' },
]

async function fetchSample({ name, type }) {
  const response = await fetch(`${import.meta.env.BASE_URL}samples/${name}`)
  // A missing file comes back as the SPA's index.html with status 200, so
  // the content type is the real check.
  const contentType = response.headers.get('content-type') || ''
  if (!response.ok || contentType.includes('text/html')) {
    throw new Error(`Sample file ${name} is missing from this build`)
  }
  return new File([await response.blob()], name, { type })
}

/** Upload the sample set into a collection. Resolves to the upload response. */
export async function addSampleSources(collectionId) {
  const files = await Promise.all(SAMPLE_FILES.map(fetchSample))
  const form = new FormData()
  for (const file of files) form.append('files', file, file.name)
  const { data } = await http.post('/documents/upload', form, {
    params: { collection_id: collectionId },
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 0,
  })
  return data
}

// The collection the welcome screen creates for the sample set, so someone
// trying Clio never has fictional records mixed into their own default.
export const SAMPLE_COLLECTION_NAME = 'Sample: equipment policy'
const SAMPLE_COLLECTION_DESCRIPTION =
  'Four fictional records for trying Clio. Delete this collection when you are done with it.'

// Hand-written questions for the sample set. Each one exercises something
// the set was built to show (see docs/demo/agency-evaluation.json): a plain
// cited answer, a scoped amendment, a conflict between sources, two facts
// the sources do not establish, and a count answered from the register.
// They need no model call, so they appear the moment the samples land and
// work as Find searches when no model is connected.
export const SAMPLE_QUESTIONS = [
  'Under EQ-17, when must shared equipment be returned?',
  'Do field teams get longer to return equipment after July 1, 2026?',
  'The desk note says five days. Why does the amendment say ten?',
  'Who approved the field-team amendment?',
  'What is the fee for a late return?',
  'How many checkouts in the register were returned late, and by which teams?',
]

const SAMPLE_NAMES = new Set(SAMPLE_FILES.map(f => f.name))

/**
 * True when the collection holds exactly the sample set and nothing else —
 * the case where the hand-written questions fit. Once someone adds their
 * own sources the model-written starters take over again.
 */
export async function isSampleSet(collectionId) {
  const { data } = await http.get('/documents', {
    params: { collection_id: collectionId, limit: SAMPLE_FILES.length + 1 },
    quiet: true,
  })
  const names = (data?.documents || []).map(d => d.filename)
  return names.length === SAMPLE_NAMES.size && names.every(n => SAMPLE_NAMES.has(n))
}

/**
 * Create (or reuse) the sample collection and fill it. Reuses an owned
 * collection with the sample name, so running the welcome screen twice does
 * not leave two copies, and uploads only into an empty one.
 * Resolves to the collection.
 */
export async function loadSampleCollection(collectionStore) {
  await collectionStore.loadCollections()
  let collection = collectionStore.ownedCollections.find(c => c.name === SAMPLE_COLLECTION_NAME)
  if (!collection) {
    collection = await collectionStore.createCollection({
      name: SAMPLE_COLLECTION_NAME,
      description: SAMPLE_COLLECTION_DESCRIPTION,
      visibility: 'private',
    })
  }
  const { data } = await http.get('/documents', {
    params: { collection_id: collection.id, limit: 1 },
    quiet: true,
  })
  if (!data?.total_documents) await addSampleSources(collection.id)
  return collection
}
