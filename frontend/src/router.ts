import { createRouter, createWebHistory } from 'vue-router'

import { useAuthStore } from './stores/auth'

const KnowledgeDetailView = () => import('./views/KnowledgeDetailView.vue')
const KnowledgeListView = () => import('./views/KnowledgeListView.vue')
const LoginView = () => import('./views/LoginView.vue')
const RegisterView = () => import('./views/RegisterView.vue')
const ModelConfigView = () => import('./views/ModelConfigView.vue')
const ApplicationEditView = () => import('./views/ApplicationEditView.vue')
const ApplicationListView = () => import('./views/ApplicationListView.vue')
const ApplicationOverviewView = () => import('./views/ApplicationOverviewView.vue')
const PublicChatView = () => import('./views/PublicChatView.vue')
const OrganizationGovernanceView = () => import('./views/OrganizationGovernanceView.vue')
const DashboardView = () => import('./views/DashboardView.vue')
const TaskCenterView = () => import('./views/TaskCenterView.vue')
const AccountSecurityView = () => import('./views/AccountSecurityView.vue')
const InvitationAcceptView = () => import('./views/InvitationAcceptView.vue')
const PasswordResetView = () => import('./views/PasswordResetView.vue')
const EmailVerificationView = () => import('./views/EmailVerificationView.vue')
const VectorIndexView = () => import('./views/VectorIndexView.vue')

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/dashboard' },
    { path: '/login', component: LoginView },
    { path: '/register', component: RegisterView },
    { path: '/reset-password', component: PasswordResetView },
    { path: '/verify-email', component: EmailVerificationView },
    { path: '/invite/:token', component: InvitationAcceptView },
    { path: '/dashboard', component: DashboardView },
    { path: '/tasks', component: TaskCenterView },
    { path: '/operations/vector-index', component: VectorIndexView },
    { path: '/account/security', component: AccountSecurityView },
    { path: '/knowledge', component: KnowledgeListView },
    { path: '/knowledge/:id', component: KnowledgeDetailView },
    { path: '/model-configs', component: ModelConfigView },
    { path: '/applications', component: ApplicationListView },
    { path: '/applications/:id', component: ApplicationEditView },
    { path: '/applications/:id/overview', component: ApplicationOverviewView },
    { path: '/organization', component: OrganizationGovernanceView },
    { path: '/share/:token', component: PublicChatView },
  ],
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  const publicPath = to.path.startsWith('/share/') || to.path.startsWith('/invite/') || ['/reset-password','/verify-email'].includes(to.path)
  if (!auth.initialized && !publicPath) await auth.initialize()
  const publicAuthPaths = ['/login', '/register']
  if (!publicPath && !publicAuthPaths.includes(to.path) && !auth.loggedIn) return '/login'
  if (publicAuthPaths.includes(to.path) && auth.loggedIn) return '/dashboard'
})

export default router
