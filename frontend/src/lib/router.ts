import { useEffect, useState } from 'react'

// A handful of pages and one parameter. The URL hash is the router: deep links work,
// the back button works, and the browser keeps the history.
// ceiling: hash routing; a router library if the page count grows past a handful.

export type Route =
  | { page: 'home' }
  | { page: 'queue' }
  | { page: 'review'; runId: string }
  | { page: 'upload' }
  | { page: 'roles'; roleId?: string }
  | { page: 'admin' }
  | { page: 'login' }

export function parse(hash: string): Route {
  const [page = 'home', id] = hash.replace(/^#\/?/, '').split('/')
  if (page === 'review' && id) return { page: 'review', runId: id }
  if (page === 'roles') return id ? { page: 'roles', roleId: id } : { page: 'roles' }
  if (page === 'queue' || page === 'upload' || page === 'admin' || page === 'login') return { page }
  return { page: 'home' }
}

export function href(route: Route): string {
  if (route.page === 'review') return `#/review/${route.runId}`
  if (route.page === 'roles' && route.roleId) return `#/roles/${route.roleId}`
  return `#/${route.page}`
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parse(window.location.hash))
  useEffect(() => {
    const onChange = () => setRoute(parse(window.location.hash))
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  return route
}

export function navigate(route: Route): void {
  window.location.hash = href(route)
}
