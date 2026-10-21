import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import Repositories from './pages/Repositories'
import Analyze from './pages/Analyze'
import Optimize from './pages/Optimize'
import PRReview from './pages/PRReview'
import Ask from './pages/Ask'
import Analytics from './pages/Analytics'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Repositories />} />
          <Route path="analyze" element={<Analyze />} />
          <Route path="optimize" element={<Optimize />} />
          <Route path="pr-review" element={<PRReview />} />
          <Route path="ask" element={<Ask />} />
          <Route path="analytics" element={<Analytics />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
