import { useEffect, useRef, useState, type ReactNode } from 'react'

/**
 * Renders `children` only once the wrapper scrolls near the viewport, keeping a
 * heavy below-the-fold subtree (e.g. the all-members coverage table) off the
 * critical initial render so the above-the-fold content paints sooner.
 *
 * Falls back to rendering immediately where IntersectionObserver is unavailable
 * (jsdom in tests, SSR), so behaviour and existing tests are unchanged there.
 */
export function DeferredMount({
  children,
  minHeight = 80,
}: {
  children: ReactNode
  minHeight?: number
}) {
  const [show, setShow] = useState(() => typeof IntersectionObserver === 'undefined')
  const ref = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (show) return
    const el = ref.current
    if (!el) return
    const io = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          setShow(true)
          io.disconnect()
        }
      },
      { rootMargin: '200px' }, // start rendering just before it scrolls into view
    )
    io.observe(el)
    return () => io.disconnect()
  }, [show])

  if (show) return <>{children}</>
  // Reserve space so deferring doesn't cause a layout jump when it mounts.
  return <div ref={ref} style={{ minHeight }} aria-hidden />
}
