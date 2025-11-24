import { useState } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";

export default function EmployerDashboard() {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<any[]>([]);
  const [qrPayload, setQrPayload] = useState("");

  const doSearch = async () => {
    try {
      const data = await api(`/employer/search?q=${encodeURIComponent(query)}`);
      setResults(data.results || []);
    } catch (err: any) {
      alert(JSON.stringify(err));
    }
  };

  const verifyFromQr = async () => {
    try {
      const parsed = JSON.parse(qrPayload);
      const data = await api("/blockchain/verify", {
        method: "POST",
        body: JSON.stringify({ student_id: parsed.student_id }),
      });
      alert(
        data.valid
          ? "Certificate is valid and verified on blockchain"
          : "Certificate is invalid or not found on blockchain",
      );
    } catch (err: any) {
      alert("Invalid QR payload or error: " + String(err?.error || err));
    }
  };

  return (
    <main className="container py-10">
      <h1 className="text-2xl font-bold">Employer Dashboard</h1>
      <p className="text-muted-foreground">
        Verify credentials by scanning QR or searching for students.
      </p>
      <div className="mt-6 grid grid-cols-1 gap-6 md:grid-cols-2">
        <div className="rounded-lg border bg-card p-6">
          <h3 className="text-lg font-semibold">Search Student Credential</h3>
          <div className="mt-4 flex gap-2">
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search by name, email, or ID"
              className="flex-1 rounded-md border px-3 py-2"
              onKeyPress={(e) => e.key === "Enter" && doSearch()}
            />
            <Button onClick={doSearch}>Search</Button>
          </div>
          {results.length === 0 && query && (
            <p className="mt-4 text-sm text-muted-foreground">
              No results found. Try searching by name or email.
            </p>
          )}
          <div className="mt-4 space-y-3">
            {results.map((r) => (
              <div
                key={r.student.id}
                className="rounded-md border p-3 space-y-2"
              >
                <div className="flex items-center justify-between">
                  <div className="flex-1">
                    <div className="font-semibold">ID: {r.student.id}</div>
                    <div className="text-xs text-muted-foreground">
                      {Object.entries(r.student.data || {}).map(([k, v]) => (
                        <div key={k}>
                          <span className="font-medium">{k}:</span> {String(v)}
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
                <div className="flex items-center justify-between text-sm">
                  <div>
                    <span
                      className={
                        r.verified
                          ? "text-green-600 font-medium"
                          : "text-yellow-600 font-medium"
                      }
                    >
                      {r.verified ? "✓ Verified" : "⚠ Unverified"}
                    </span>
                  </div>
                  {r.proof && (
                    <div className="text-xs text-muted-foreground">
                      Hash: {r.proof.hash?.substring(0, 16)}...
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
        <div className="rounded-lg border bg-card p-6">
          <h3 className="text-lg font-semibold">Verify via QR Code</h3>
          <p className="text-sm text-muted-foreground mt-2">
            Scan a student's QR code and paste the payload below to verify their
            credential on the blockchain.
          </p>
          <textarea
            value={qrPayload}
            onChange={(e) => setQrPayload(e.target.value)}
            placeholder='Paste QR JSON payload like {"student_id":1}'
            className="mt-3 h-40 w-full rounded-md border p-3 text-sm"
          />
          <div className="mt-3 flex justify-end">
            <Button onClick={verifyFromQr}>Verify Blockchain</Button>
          </div>
        </div>
      </div>
    </main>
  );
}
