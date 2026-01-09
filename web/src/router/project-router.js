// 独立的项目路由配置
import ProjectLayout from '@/layouts/ProjectLayout.vue'

const projectRoutes = [
  {
    path: '/project',
    name: 'ProjectMain',
    component: ProjectLayout,
    meta: { requiresAuth: true },
    children: [
      {
        path: '',
        name: 'StartPage',
        component: () => import('../views/project/StartPage.vue'),
        meta: { keepAlive: true }
      },
      {
        path: 'list',
        name: 'ProjectManagement',
        component: () => import('../views/project/ProjectManagement.vue'),
        meta: { keepAlive: true }
      },
      {
        path: 'demo',
        children: [
          {
            path: '',
            name: 'ProjectDemo',
            component: () => import('../views/project/ProjectManagement.vue'),
            meta: { keepAlive: true, isDemo: true }
          },
          {
            path: ':id',
            name: 'ProjectDemoDetail',
            component: () => import('../views/project/ProjectDetail.vue'),
            meta: { keepAlive: false, isDemo: true }
          },
          {
            path: ':id/generate',
            name: 'ProjectDemoGenerate',
            component: () => import('../views/project/DocumentGenerator.vue'),
            meta: { keepAlive: false, isDemo: true }
          }
        ]
      },
      {
        path: ':id',
        name: 'ProjectDetail',
        component: () => import('../views/project/ProjectDetail.vue'),
        meta: { keepAlive: false }
      },
      {
        path: ':id/generate',
        name: 'DocumentGenerator',
        component: () => import('../views/project/DocumentGenerator.vue'),
        meta: { keepAlive: false }
      }
    ]
  }
]

export default projectRoutes