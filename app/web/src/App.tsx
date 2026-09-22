import { Route, Routes } from 'react-router-dom';
import { Layout } from './components/Layout';
import { FindingDetailPage } from './pages/FindingDetailPage';
import { FindingsPage } from './pages/FindingsPage';
import { HomePage } from './pages/HomePage';
import { RulesPage } from './pages/RulesPage';
import { RunsPage } from './pages/RunsPage';

export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<HomePage />} />
        <Route path="/findings" element={<FindingsPage />} />
        <Route path="/findings/:id" element={<FindingDetailPage />} />
        <Route path="/rules" element={<RulesPage />} />
        <Route path="/runs" element={<RunsPage />} />
        <Route
          path="*"
          element={
            <>
              <h1>Page not found</h1>
              <p>This address does not match a screen.</p>
            </>
          }
        />
      </Route>
    </Routes>
  );
}
