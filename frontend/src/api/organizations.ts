import client from './client'
import type { PageResult } from './common'

export type OrganizationRole = 'OWNER' | 'ADMIN' | 'MEMBER' | 'AUDITOR'
export type WorkspaceRole = 'ADMIN' | 'DEVELOPER' | 'OPERATOR' | 'VIEWER' | 'AUDITOR'

export interface OrganizationItem {
  id: number
  name: string
  slug: string
  status: 'ACTIVE' | 'DISABLED'
  current_user_role: OrganizationRole
  member_count: number
  workspace_count: number
  created_at: string
  updated_at: string
}

export interface WorkspaceItem {
  id: number
  organization_id: number
  organization_name: string
  name: string
  slug: string
  status: 'ACTIVE' | 'DISABLED'
  is_default: boolean
  current_user_role: WorkspaceRole
  capabilities: string[]
  member_count: number
  created_at: string
  updated_at: string
}

export interface OrganizationMembershipItem {
  id: number
  user_id: number
  username: string
  role: OrganizationRole
  created_at: string
  updated_at: string
}

export interface WorkspaceMembershipItem {
  id: number
  user_id: number
  username: string
  role: WorkspaceRole
  created_at: string
  updated_at: string
}

export interface AuditEventItem {
  id: number
  organization_id: number
  workspace_id: number | null
  workspace_name: string | null
  actor_id: number | null
  actor_username: string | null
  action: string
  resource_type: string
  resource_id: string
  result: 'SUCCESS' | 'FAILURE' | 'REJECTED'
  request_id: string
  ip_hash: string
  metadata: Record<string, unknown>
  created_at: string
}

export interface MeContext {
  organizations: OrganizationItem[]
  workspaces: WorkspaceItem[]
  active_workspace_id: number | null
}

export async function getMeContext(): Promise<MeContext> {
  return (await client.get('/me/context/')).data.data
}

export async function listOrganizations(): Promise<PageResult<OrganizationItem>> {
  return (await client.get('/organizations/', { params: { page_size: 100 } })).data.data
}

export async function createOrganization(name: string): Promise<OrganizationItem> {
  return (await client.post('/organizations/', { name })).data.data
}

export async function listOrganizationMembers(id: number): Promise<PageResult<OrganizationMembershipItem>> {
  return (await client.get(`/organizations/${id}/members/`, { params: { page_size: 100 } })).data.data
}

export async function addOrganizationMember(id: number, username: string, role: OrganizationRole) {
  return (await client.post(`/organizations/${id}/members/`, { username, role })).data.data as OrganizationMembershipItem
}

export async function updateOrganizationMember(id: number, membershipId: number, role: OrganizationRole) {
  return (await client.patch(`/organizations/${id}/members/${membershipId}/`, { role })).data.data as OrganizationMembershipItem
}

export async function removeOrganizationMember(id: number, membershipId: number) {
  await client.delete(`/organizations/${id}/members/${membershipId}/`)
}

export async function listOrganizationWorkspaces(id: number): Promise<PageResult<WorkspaceItem>> {
  return (await client.get(`/organizations/${id}/workspaces/`, { params: { page_size: 100 } })).data.data
}

export async function createWorkspace(id: number, name: string): Promise<WorkspaceItem> {
  return (await client.post(`/organizations/${id}/workspaces/`, { name })).data.data
}

export async function listWorkspaceMembers(id: number): Promise<PageResult<WorkspaceMembershipItem>> {
  return (await client.get(`/workspaces/${id}/members/`, {
    params: { page_size: 100 }, headers: { 'X-Workspace-ID': String(id) },
  })).data.data
}

export async function addWorkspaceMember(id: number, username: string, role: WorkspaceRole) {
  return (await client.post(`/workspaces/${id}/members/`, { username, role }, {
    headers: { 'X-Workspace-ID': String(id) },
  })).data.data as WorkspaceMembershipItem
}

export async function updateWorkspaceMember(id: number, membershipId: number, role: WorkspaceRole) {
  return (await client.patch(`/workspaces/${id}/members/${membershipId}/`, { role }, {
    headers: { 'X-Workspace-ID': String(id) },
  })).data.data as WorkspaceMembershipItem
}

export async function removeWorkspaceMember(id: number, membershipId: number) {
  await client.delete(`/workspaces/${id}/members/${membershipId}/`, {
    headers: { 'X-Workspace-ID': String(id) },
  })
}

export async function listAuditEvents(params: Record<string, string | number> = {}): Promise<PageResult<AuditEventItem>> {
  return (await client.get('/audit-events/', { params })).data.data
}
