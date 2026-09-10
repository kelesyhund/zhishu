import { createRouter, createWebHistory } from 'vue-router'

import KnowledgeDetailView from './views/KnowledgeDetailView.vue'
import KnowledgeListView from './views/KnowledgeListView.vue'
import LoginView from './views/LoginView.vue'
import RegisterView from './views/RegisterView.vue'
import ModelConfigView from './views/ModelConfigView.vue'
import ApplicationEditView from './views/ApplicationEditView.vue'
import ApplicationListView from './views/ApplicationListView.vue'
import ApplicationOverviewView from './views/ApplicationOverviewView.vue'
import PublicChatView from './views/PublicChatView.vue'
import OrganizationGovernanceView from './views/OrganizationGovernanceView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/knowledge' },
    { path: '/login', component: LoginView },
    { path: '/register', component: RegisterView },
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

router.beforeEach((to) => {
  const token = localStorage.getItem('token')
  if (to.path.startsWith('/share/')) return true
  const publicAuthPaths = ['/login', '/register']
  if (!publicAuthPaths.includes(to.path) && !token) return '/login'
  if (publicAuthPaths.includes(to.path) && token) return '/knowledge'
})

export default router
