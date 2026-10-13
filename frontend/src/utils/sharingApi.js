import http from './http'

export async function createShare(collectionId, permission = 'read', expiresDays = null, notifyEmail = null) {
  const response = await http.post(`/api/collections/${collectionId}/share`, {
    permission,
    expires_days: expiresDays,
    notify_email: notifyEmail || undefined,
  })
  return response.data
}

export async function createSharesBulk(collectionId, emails, permission = 'read', expiresDays = null) {
  const response = await http.post(`/api/collections/${collectionId}/shares/bulk`, {
    emails,
    permission,
    expires_days: expiresDays,
  })
  return response.data
}

export async function listShares(collectionId) {
  const response = await http.get(`/api/collections/${collectionId}/shares`)
  return response.data.shares || []
}

export async function acceptShare(shareToken) {
  const response = await http.post(`/api/shares/${shareToken}/accept`)
  return response.data
}

export async function revokeShare(shareId) {
  const response = await http.delete(`/api/shares/${shareId}`)
  return response.data
}
