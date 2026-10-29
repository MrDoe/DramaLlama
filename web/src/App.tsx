import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Shell } from "./components/Shell";
import { NewProjectPage } from "./pages/NewProjectPage";
import { ProjectPage } from "./pages/ProjectPage";
import { ProjectsPage } from "./pages/ProjectsPage";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route
          path="/"
          element={
            <Shell>
              <ProjectsPage />
            </Shell>
          }
        />
        <Route
          path="/new"
          element={
            <Shell>
              <NewProjectPage />
            </Shell>
          }
        />
        <Route
          path="/projects/:id"
          element={
            <Shell>
              <ProjectPage />
            </Shell>
          }
        />
      </Routes>
    </BrowserRouter>
  );
}
