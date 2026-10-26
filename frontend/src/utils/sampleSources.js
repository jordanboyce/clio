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
