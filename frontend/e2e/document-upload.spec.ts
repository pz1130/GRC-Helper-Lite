import { expect, test } from "@playwright/test";

// 一份合法 DOCX：标题是朴素的数字编号，不带可选的编号组件。
const documentBytes = Buffer.from("UEsDBBQAAAAIABNrMl33S4B1wgAAAHYBAAATAAAAW0NvbnRlbnRfVHlwZXNdLnhtbH2QuQ7CMAyGX6XKiqgRAwOiLMAKDLyAlbptRC7FLsfbk3INCBjt//gsLw7XSFxcnPVcqU4kzgFYd+SQyxDJZ6UJyaHkMbUQUR+xJZhOJjPQwQt5GcvQoZaLNTXYWyk2l7xmE3ylEllWxephHFiVwhit0ShZh5OvPyjjJ6HMybuHOxN5lA0KvhIG5TfgmdudKCVTU7HHJFt02QXnkGqog+5dTpb/a77cGZrGaHrnh7aYgiZm41tny7fi0PjX/XB/9/IGUEsDBBQAAAAIABNrMl1hey9DiQAAAPIAAAALAAAAX3JlbHMvLnJlbHONzzsOAiEQBuCrEA6ws1pYGKCy2dZ4AQLDIy6PDBj19lJYrMbCcuaffH9GnHHVPZbcQqyNPdKam+Sh93oEaCZg0m0qFfNIXKGk+xjJQ9Xmqj3Cfp4PQFuDK7E12WIlp8XuOLs8K/5jF+eiwVMxt4S5/6j4uhiyJo9d8nshC/a9ngbLQQn4eFG9AFBLAwQUAAAACAATazJd3s6TXakAAAA+AQAAEQAAAHdvcmQvZG9jdW1lbnQueG1shZDdCoMwDIVfJfgAq/NiF+J8CPcEtc200L+lcZ1vPyuMwRh484VDTnJIutzqoBaHnuHlrE9tvlYzc2yFSGpGJ9MpRPRb7x7ISd4kTSIH0pGCwpSMn5wVTV1fhJPGV32X2zHotdRYQAXcn+Gmtk2dKKKQdsZf3yAZwRpneNsMbkkMIwJ6OVrUp8PxBgZ8GszHObsNeCZMc7A6wWORxEh2/ZciPleJ78f6N1BLAQIUAxQAAAAIABNrMl33S4B1wgAAAHYBAAATAAAAAAAAAAAAAACAAQAAAABbQ29udGVudF9UeXBlc10ueG1sUEsBAhQDFAAAAAgAE2syXWF7L0OJAAAA8gAAAAsAAAAAAAAAAAAAAIAB8wAAAF9yZWxzLy5yZWxzUEsBAhQDFAAAAAgAE2syXd7Ok12pAAAAPgEAABEAAAAAAAAAAAAAAIABpQEAAHdvcmQvZG9jdW1lbnQueG1sUEsFBgAAAAADAAMAuQAAAH0CAAAAAA==", "base64");

test("uploaded DOCX is parsed by the worker and becomes keyword searchable", async ({ request }) => {
  const api = process.env.E2E_API_BASE ?? "http://localhost:8000";
  const login = await request.post(`${api}/api/auth/login`, {
    data: { email: "admin@example.com", password: "pw123456" },
  });
  expect(login.ok()).toBeTruthy();
  const headers = { Authorization: `Bearer ${(await login.json()).access_token}` };
  const upload = await request.post(`${api}/api/documents`, {
    headers,
    multipart: {
      title: "DOCX numbering regression", doc_type: "standard",
      file: { name: "numbered.docx", mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", buffer: documentBytes },
    },
  });
  expect(upload.status()).toBe(201);
  const documentId = (await upload.json()).id;
  await expect.poll(async () => {
    const response = await request.get(`${api}/api/documents/${documentId}`, { headers });
    return (await response.json()).status;
  }, { timeout: 20000 }).toBe("active");
  const clauses = await request.get(`${api}/api/documents/${documentId}/clauses`, { headers });
  const parsedClauses = await clauses.json();
  expect(parsedClauses.map((item: { citation_label: string }) => item.citation_label)).toEqual(["1", "2"]);
  const sources = await request.get(`${api}/api/documents/${documentId}/source-blocks`, { headers });
  expect(sources.ok()).toBeTruthy();
  const snapshot = await sources.json();
  expect(snapshot.available).toBe(true);
  expect(snapshot.total).toBeGreaterThan(0);
  expect(snapshot.unassigned).toBe(0);
  const clauseSources = await request.get(`${api}/api/clauses/${parsedClauses[0].id}/source-blocks`, { headers });
  expect(clauseSources.ok()).toBeTruthy();
  const original = (await clauseSources.json()).blocks[0];
  expect(original.origin).toBe("docx_xml");
  expect(original.locator.body_index).toBeGreaterThan(0);
  await expect.poll(async () => {
    const response = await request.get(`${api}/api/search?q=rate%20limiting&document_id=${documentId}`, { headers });
    return (await response.json()).hits.length;
  }, { timeout: 20000 }).toBeGreaterThan(0);
});
