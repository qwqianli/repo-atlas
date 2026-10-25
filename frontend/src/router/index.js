import { createRouter, createWebHistory } from 'vue-router'
import HomeView from '../views/HomeView.vue'
import TaskView from '../views/TaskView.vue'
import NotFoundView from '../views/NotFoundView.vue'

// 首页输入仓库，结果页按 task_id 展示；任务表在后端内存里，路由参数就是唯一凭证
export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: HomeView },
    { path: '/tasks/:id', name: 'task', component: TaskView, props: true },
    // 通配 404：不写它，手滑输错地址就是白屏
    { path: '/:pathMatch(.*)*', name: 'not-found', component: NotFoundView },
  ],
})
