// Oberflaeche der Notiz-App
import { api } from "./api.js";

export function render(notes) {
  const list = document.getElementById("notes");
  list.innerHTML = notes.map((n) => `<li>${n}</li>`).join("");
}

export class App {
  constructor(root) {
    this.root = root;
  }

  async refresh() {
    const notes = await api.get("/notes");
    render(notes);
  }
}
