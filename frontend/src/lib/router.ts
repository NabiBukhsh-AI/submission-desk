import { useEffect, useState } from 'react'

// A handful of pages and one parameter. The URL hash is the router: deep links work,
// the back button works, and the browser keeps the history.
// ponytail: hash routing; a router library if the page count grows past a handful.

export type Route =
  | { page: 'queue' }
  | { page: 'review'; runId: string }
  | { page: 'upload' }
  | { page: 'admin' }
  | { page: 'login' }

export function parse(hash: string): Route {
  const [page = 'queue', id] = hash.replace(/^#\/?/, '').split('/')
  if (page === 'review' && id) return { page: 'review', runId: id }
  if (page === 'upload' || page === 'admin' || page === 'login') return { page }
  return { page: 'queue' }
}

export function href(route: Route): string {
  if (route.page === 'review') return `#/review/${route.runId}`
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
