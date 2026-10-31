"""R033: Visual Basic - VB.NET (vb) und VBA/VB6 (vba)."""

from helpers import ProjectCase, doc

from pypatcher import adapters
from pypatcher.adapters.base import ParseError
from pypatcher.profile import default_mode

VB = adapters.get("vb")
VBA = adapters.get("vba")
PROF = {"comments": "ignore", "whitespace": "ignore"}

NET = '''Imports System

Namespace App.Notes
    ' Speicher fuer Notizen
    <Serializable>
    Public Class NoteStore
        Implements IStore

        Private _notes As New List(Of String)
        Public Property Count As Integer

        Public Sub New()
            _notes.Clear()
        End Sub

        Public Property Title As String
            Get
                Return _title
            End Get
            Set(value As String)
                _title = value
            End Set
        End Property

        Public Function Find(word As String) As List(Of String)
            Dim result = _notes.Where(Function(n) n.Contains(word)).ToList()
            If result.Count = 0 Then Return Nothing
            Return result
        End Function

        Public Function Find(word As String, limit As Integer) As List(Of String)
            Return Find(word).Take(limit).ToList()
        End Function

        Private Declare Function Beep Lib "kernel32" (ByVal f As Integer) As Integer
    End Class

    Public Interface IStore
        Sub Add(text As String)
    End Interface

    Public Module Helpers
        Public Sub Log(msg As String)
            Console.WriteLine($"[{Now:HH:mm}] {msg}")
        End Sub
    End Module
End Namespace
'''

KONTO = '''VERSION 1.0 CLASS
BEGIN
  MultiUse = -1  'True
END
Attribute VB_Name = "Konto"
Option Explicit

Private mSaldo As Currency

' Saldo lesen
Public Property Get Saldo() As Currency
    Saldo = mSaldo
End Property

Public Property Let Saldo(ByVal wert As Currency)
    mSaldo = wert
End Property

Public Sub Einzahlen(ByVal betrag As Currency)
    If betrag <= 0 Then Exit Sub
    mSaldo = mSaldo + betrag: Debug.Print "ok"
    REM Kommentar
    MsgBox "Datum: " & #1/2/2020# & _
        " Fertig"
End Sub

Private Type Eintrag
    Name As String * 20
End Type

Private Declare PtrSafe Function GetTickCount Lib "kernel32" () As Long
'''


class VisualBasic(ProjectCase):

    def test_modes(self):
        self.assertEqual(default_mode("a.vb"), "vb")
        for name in ("a.bas", "a.cls", "a.frm", "a.vbs"):
            self.assertEqual(default_mode(name), "vba")

    def test_vbnet_units(self):
        info = VB.analyze(doc(NET))
        units = dict((u.path, u.kind) for u in info.units)
        for path, kind in (("App.Notes", "namespace"), ("App.Notes.NoteStore", "class"),
                           ("App.Notes.NoteStore.New", "sub"),
                           ("App.Notes.NoteStore.Title", "property"),
                           ("App.Notes.NoteStore.Find#1", "function"),
                           ("App.Notes.NoteStore.Find#2", "function"),
                           ("App.Notes.IStore", "interface"), ("App.Notes.Helpers.Log", "sub")):
            self.assertEqual(units.get(path), kind, path)
        for absent in ("App.Notes.NoteStore.Count", "App.Notes.NoteStore.Beep",
                       "App.Notes.IStore.Add"):
            self.assertNotIn(absent, units)
        store = next(u for u in info.units if u.path == "App.Notes.NoteStore")
        self.assertEqual(info.lines[store.start].strip(), "' Speicher fuer Notizen")
        self.assertEqual(VB.find(info, "app.notes.notestore.new").path, "App.Notes.NoteStore.New")

    def test_vba_units_and_header(self):
        info = VBA.analyze(doc(KONTO))
        self.assertEqual([(u.path, u.start, u.end) for u in info.units],
                         [("@header", 0, 5), ("Saldo#1", 9, 13), ("Saldo#2", 14, 17),
                          ("Einzahlen", 18, 25), ("Eintrag", 26, 29)])

    def test_normal_form(self):
        changed = KONTO.replace("Public Sub Einzahlen", "public sub einzahlen") \
            .replace("REM Kommentar", "' anders").replace("& _\n        \" Fertig\"", '& " Fertig"')
        self.assertEqual(VBA.normalize(doc(KONTO), PROF), VBA.normalize(doc(changed), PROF))
        self.assertNotEqual(VBA.normalize(doc(KONTO), PROF),
                            VBA.normalize(doc(KONTO.replace('"ok"', '"OK"')), PROF))

    def test_syntax_errors(self):
        with self.assertRaises(ParseError):
            VB.validate(doc("Public Sub A()\nEnd Function\n"))
        with self.assertRaises(ParseError):
            VB.validate(doc("Public Class A\n    Sub B()\n    End Sub\n"))

    def test_patch_vba(self):
        self.write("Konto.cls", KONTO)
        self.write("app/Store.vb", NET)
        self.make_target()
        draft = self.write("d.ppatch", "#PPATCH 1 v 1/1\nTITLE t\nFILE Konto.cls\n"
                           "  UNIT Einzahlen @1\n  UADD after:Einzahlen @2\n"
                           "FILE app/Store.vb\n  UADD end-of:App.Notes.NoteStore @3\n"
                           "@1 .\nPublic Sub Einzahlen(ByVal betrag As Currency)\n"
                           "    mSaldo = mSaldo + betrag\nEnd Sub\n@.\n"
                           "@2 .\nPublic Sub Abheben(ByVal betrag As Currency)\n"
                           "    mSaldo = mSaldo - betrag\nEnd Sub\n@.\n"
                           "@3 .\nPublic Sub Clear()\n    _notes.Clear()\nEnd Sub\n@.\n",
                           root=self.tmp)
        self.assertEqual(self.run_cli("run", "-y", draft, "-p", self.target), 0, self.last_err)
        text = self.read("Konto.cls").decode()
        self.assertIn("    mSaldo = mSaldo + betrag\nEnd Sub\n\nPublic Sub Abheben(", text)
        net = self.read("app/Store.vb").decode()
        self.assertIn("        Private Declare Function Beep Lib \"kernel32\" (ByVal f As Integer)"
                      " As Integer\n\n        Public Sub Clear()\n            _notes.Clear()\n"
                      "        End Sub\n    End Class\n", net)
