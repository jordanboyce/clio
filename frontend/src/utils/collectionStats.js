import http from './http'

// Collection-level numbers and the file list download. The server computes
// both in SQL over every servable document, so they stay exact and cheap at
// any collection size; never derive them from the /documents listing.

export const fetchCollectionStats = async (collectionId) =>
  (await http.get('/documents/stats', { params: { collection_id: collectionId } })).data

// A plain URL, not an http.get: the browser streams the download itself, so a
// list of tens of thousands of files never sits in memory here.
export const fileListUrl = (collectionId, { format = 'csv', fileType = '' } = {}) => {
  const params = new URLSearchParams({ collection_id: collectionId, format })
  if (fileType) params.set('file_type', fileType)
  return `/documents/export?${params}`
}

export const heldNote = (held) =>
  `${held.toLocaleString()} held for review ${held === 1 ? 'is' : 'are'} left out.`
