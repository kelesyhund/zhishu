import client from './client'
import type { OrganizationRole, WorkspaceRole } from './organizations'

export interface CurrentUser {
  id: number
  username: string
  email_masked: string
  email_configured: boolean
  email_verified: boolean
}

export async function initializeCsrf(): Promise<void> {
  await client.get('/auth/csrf/')
}

export async function getCurrentUser(): Promise<CurrentUser> {
  return (await client.get('/auth/me/')).data.data
}

export async function login(username: string, password: string): Promise<CurrentUser> {
  await initializeCsrf()
  await client.post('/auth/login/', { username, password })
  return getCurrentUser()
}

export async function logout(): Promise<void> {
  await client.post('/auth/logout/')
}

export interface RegisterPayload {
  username: string
  email?: string
  password: string
  password_confirm: string
}

export interface AuthResult {
  id: number
  username: string
}

export async function register(payload: RegisterPayload): Promise<AuthResult> {
  await initializeCsrf()
  return (await client.post('/auth/register/', payload)).data.data
}

export interface AccountSessionItem {
  id: string; user_agent: string; ip_summary: string; created_at: string; last_seen_at: string; current: boolean
}
export async function listAccountSessions(): Promise<{ items: AccountSessionItem[]; total: number }> {
  return (await client.get('/auth/sessions/')).data.data
}
export async function revokeAccountSession(id: string): Promise<void> { await client.delete(`/auth/sessions/${id}/`) }
export async function logoutOtherSessions(): Promise<number> {
  return (await client.post('/auth/sessions/logout-others/')).data.data.revoked_count
}
export async function changePassword(currentPassword: string, newPassword: string): Promise<void> {
  await client.post('/auth/change-password/', { current_password: currentPassword, new_password: newPassword })
}
export async function requestPasswordReset(email: string): Promise<void> {
  await initializeCsrf(); await client.post('/auth/password-reset/request/', { email })
}
export async function confirmPasswordReset(uid: string, token: string, newPassword: string): Promise<void> {
  await initializeCsrf(); await client.post('/auth/password-reset/confirm/', { uid, token, new_password: newPassword })
}
export async function requestEmailVerification(): Promise<void> { await client.post('/auth/email-verification/request/') }
export async function confirmEmailVerification(token: string): Promise<void> {
  await initializeCsrf(); await client.post('/auth/email-verification/confirm/', { token })
}

export interface InvitationItem {
  id: number; email_masked: string; organization_role: OrganizationRole; status: string
  expires_at: string; workspace_grants: { workspace_id: number; workspace_name: string; role: WorkspaceRole }[]; created_at: string
  invitation_url?: string
}
export async function listInvitations(organizationId: number): Promise<{ items: InvitationItem[]; total: number }> {
  return (await client.get(`/organizations/${organizationId}/invitations/`)).data.data
}
export async function createInvitation(organizationId: number, payload: {
  email: string; organization_role: OrganizationRole; workspace_grants: { workspace_id: number; role: WorkspaceRole }[]
}): Promise<InvitationItem> {
  return (await client.post(`/organizations/${organizationId}/invitations/`, payload)).data.data
}
export async function invitationAction(organizationId: number, invitationId: number, action: 'revoke' | 'resend'): Promise<InvitationItem> {
  return (await client.post(`/organizations/${organizationId}/invitations/${invitationId}/${action}/`)).data.data
}
export interface InvitationPreview { organization_name: string; email_masked: string; expires_at: string; status: string }
export async function previewInvitation(token: string): Promise<InvitationPreview> {
  return (await client.get(`/invitations/${encodeURIComponent(token)}/preview/`)).data.data
}
export async function acceptInvitation(token: string): Promise<void> {
  await initializeCsrf(); await client.post(`/invitations/${encodeURIComponent(token)}/accept/`)
}
export async function registerAndAcceptInvitation(token: string, payload: Omit<RegisterPayload, 'email'>): Promise<AuthResult> {
  await initializeCsrf()
  return (await client.post(`/invitations/${encodeURIComponent(token)}/register-and-accept/`, payload)).data.data
}
