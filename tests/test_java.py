"""R032: Java."""

from helpers import ProjectCase, doc

from pypatcher import adapters

JAVA = adapters.get("java")

SOURCE = '''package app;

import java.util.List;

/**
 * Notizen.
 */
@Service
public class NoteStore<T extends Note> implements Store {
    private static final String QUERY = """
        SELECT * FROM notes
        WHERE id = ?
        """;
    private final List<T> notes = new ArrayList<>();

    static {
        init();
    }

    public NoteStore() {
        this(10);
    }

    public NoteStore(int size) {
        notes = new ArrayList<>(size);
    }

    /** Fuegt hinzu. */
    @Override
    @SuppressWarnings("unchecked")
    public synchronized <R> R add(T note) throws StoreException {
        notes.add(note);
        return (R) note;
    }

    public int find(String word) {
        return notes.stream().filter(n -> n.contains(word)).count() > 0 ? 1 : 0;
    }

    public int find(String word, int limit) {
        Runnable r = new Runnable() {
            public void run() {
                System.out.println(word);
            }
        };
        return limit;
    }

    public static class Entry {
        int size() {
            return 0;
        }
    }

    enum Kind {
        A, B;

        String label() {
            return name();
        }
    }
}

record Point(int x, int y) {
    Point {
        if (x < 0) throw new IllegalArgumentException();
    }

    int sum() {
        return x + y;
    }
}

@interface Marker {
    String value() default "";
}

interface Shape {
    double area();

    default String name() {
        return "shape";
    }
}
'''


class JavaUnits(ProjectCase):

    def test_units(self):
        info = JAVA.analyze(doc(SOURCE))
        units = dict((u.path, u.kind) for u in info.units)
        expected = {
            "NoteStore": "class", "NoteStore.NoteStore#1": "method",
            "NoteStore.NoteStore#2": "method", "NoteStore.add": "method",
            "NoteStore.find#1": "method", "NoteStore.find#2": "method",
            "NoteStore.Entry": "class", "NoteStore.Entry.size": "method",
            "NoteStore.Kind": "enum", "NoteStore.Kind.label": "method",
            "Point": "record", "Point.sum": "method", "Marker": "annotation",
            "Shape": "interface", "Shape.name": "method"}
        for path, kind in expected.items():
            self.assertEqual(units.get(path), kind, path)
        self.assertNotIn("NoteStore.find#2.run", units)
        store = next(u for u in info.units if u.path == "NoteStore")
        self.assertEqual(info.lines[store.start], "/**")
        add = next(u for u in info.units if u.path == "NoteStore.add")
        self.assertEqual(info.lines[add.start].strip(), "/** Fuegt hinzu. */")

    def test_text_block_counts(self):
        prof = {"comments": "ignore", "whitespace": "ignore"}
        self.assertNotEqual(JAVA.normalize(doc(SOURCE), prof),
                            JAVA.normalize(doc(SOURCE.replace("WHERE id", "WHERE  id")), prof))
        self.assertEqual(JAVA.normalize(doc(SOURCE), prof),
                         JAVA.normalize(doc(SOURCE.replace("/** Fuegt hinzu. */", "")), prof))

    def test_patch(self):
        self.write("src/NoteStore.java", SOURCE)
        self.make_target()
        draft = self.write("d.ppatch", "#PPATCH 1 j 1/1\nTITLE t\nFILE src/NoteStore.java\n"
                           "  UNIT NoteStore.find#1 @1\n  UADD end-of:NoteStore.Entry @2\n"
                           "  UCOPY Point.sum after:Point.sum as=diff\n"
                           "@1 .\npublic int find(String word) {\n    return 0;\n}\n@.\n"
                           "@2 .\n@Override\npublic String toString() {\n    return \"Entry\";\n}\n@.\n",
                           root=self.tmp)
        self.assertEqual(self.run_cli("run", "-y", draft, "-p", self.target), 0, self.last_err)
        text = self.read("src/NoteStore.java").decode()
        self.assertIn("    public int find(String word) {\n        return 0;\n    }\n", text)
        self.assertIn("            return 0;\n        }\n\n        @Override\n"
                      "        public String toString() {\n", text)
        self.assertIn("    int diff() {\n        return x + y;\n    }\n", text)
