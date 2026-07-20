"""
Smoke test cho luồng end-to-end: Crawl -> Review -> Import -> Search -> Chat

Tuần 3 - Slice 1: Smoke test script
"""

import asyncio
import sys
from datetime import datetime
from typing import Optional

# Test configuration
TEST_CONFIG = {
    "api_base_url": "http://127.0.0.1:5055",
    "admin_user": "admin",
    "admin_password": "gfi",
    "test_timeout": 30,
}

class TestResult:
    def __init__(self, name: str, passed: bool, message: str = "", duration_ms: float = 0):
        self.name = name
        self.passed = passed
        self.message = message
        self.duration_ms = duration_ms
    
    def __str__(self):
        status = "PASS" if self.passed else "FAIL"
        return f"[{status}] {self.name} ({self.duration_ms:.0f}ms) - {self.message}"


class E2ETest:
    """End-to-end test runner"""
    
    def __init__(self):
        self.results: list[TestResult] = []
        self.token: Optional[str] = None
        self.candidate_id: Optional[str] = None
        self.source_id: Optional[str] = None

    def _headers(self, role: str = "admin", json_content: bool = False) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.token}", "X-User-Role": role}
        if json_content:
            headers["Content-Type"] = "application/json"
        return headers
    
    async def run_all(self):
        """Run all test steps"""
        print("=" * 60)
        print("SMOKE TEST: Legal Import -> Auto Import Flow")
        print("=" * 60)
        print()
        
        # Step 1: Login
        await self.test_login()
        if not self.token:
            print("Login failed - stopping tests")
            return
        
        # Step 2: Check for pending candidates (or create mock)
        await self.test_list_candidates()
        
        # Step 3: Test candidate review + auto-import
        if self.candidate_id:
            await self.test_review_and_import()
        else:
            print("No candidates to test - skipping review/import")
        
        # Step 4: Test search
        await self.test_search()
        
        # Step 5: Test chat
        await self.test_chat()
        
        # Summary
        self.print_summary()
    
    async def test_login(self):
        """Test admin login"""
        import aiohttp
        
        start = datetime.now()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{TEST_CONFIG['api_base_url']}/api/auth/login",
                    json={
                        "password": TEST_CONFIG["admin_password"],
                        "role": "admin"
                    },
                    timeout=aiohttp.ClientTimeout(total=TEST_CONFIG["test_timeout"])
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        self.token = data.get("token")
                        duration = (datetime.now() - start).total_seconds() * 1000
                        self.results.append(TestResult(
                            "1. Admin Login", True, 
                            f"Token received: {self.token[:20]}...", duration
                        ))
                    else:
                        duration = (datetime.now() - start).total_seconds() * 1000
                        self.results.append(TestResult(
                            "1. Admin Login", False, 
                            f"Status {resp.status}", duration
                        ))
        except Exception as e:
            duration = (datetime.now() - start).total_seconds() * 1000
            self.results.append(TestResult(
                "1. Admin Login", False, str(e), duration
            ))
    
    async def test_list_candidates(self):
        """Test listing crawl candidates"""
        import aiohttp
        
        if not self.token:
            self.results.append(TestResult("2. List Candidates", False, "No token"))
            return
        
        start = datetime.now()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{TEST_CONFIG['api_base_url']}/api/legal/crawl/candidates",
                    headers=self._headers(),
                    timeout=aiohttp.ClientTimeout(total=TEST_CONFIG["test_timeout"])
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        candidates = data if isinstance(data, list) else data.get("candidates", data.get("results", []))
                        
                        # Find first pending candidate
                        for c in candidates:
                            if c.get("status") == "pending_review":
                                self.candidate_id = c.get("id")
                                break
                        
                        duration = (datetime.now() - start).total_seconds() * 1000
                        msg = f"Found {len(candidates)} candidates"
                        if self.candidate_id:
                            msg += f", selected: {self.candidate_id[:20]}..."
                        self.results.append(TestResult("2. List Candidates", True, msg, duration))
                    else:
                        duration = (datetime.now() - start).total_seconds() * 1000
                        self.results.append(TestResult("2. List Candidates", False, 
                            f"Status {resp.status}", duration))
        except Exception as e:
            duration = (datetime.now() - start).total_seconds() * 1000
            self.results.append(TestResult("2. List Candidates", False, str(e), duration))
    
    async def test_review_and_import(self):
        """Test review candidate -> auto import"""
        import aiohttp
        
        if not self.token or not self.candidate_id:
            self.results.append(TestResult("3. Review & Import", False, "Missing token/candidate"))
            return
        
        start = datetime.now()
        try:
            async with aiohttp.ClientSession() as session:
                # Approve candidate
                async with session.post(
                    f"{TEST_CONFIG['api_base_url']}/api/legal/crawl/candidates/{self.candidate_id}/review",
                    headers=self._headers(json_content=True),
                    json={"decision": "approved", "review_note": "Test auto-import"},
                    timeout=aiohttp.ClientTimeout(total=TEST_CONFIG["test_timeout"])
                ) as resp:
                    if resp.status in [200, 202]:
                        # Wait a moment for import to process
                        await asyncio.sleep(2)
                        
                        # Check if candidate status changed to imported
                        async with session.get(
                            f"{TEST_CONFIG['api_base_url']}/api/legal/crawl/candidates/{self.candidate_id}",
                            headers=self._headers()
                        ) as check_resp:
                            if check_resp.status == 200:
                                data = await check_resp.json()
                                new_status = (data.get("candidate") or data).get("status")
                                
                                if new_status == "imported":
                                    duration = (datetime.now() - start).total_seconds() * 1000
                                    self.results.append(TestResult(
                                        "3. Review & Auto-Import", True,
                                        f"Candidate imported successfully", duration
                                    ))
                                else:
                                    duration = (datetime.now() - start).total_seconds() * 1000
                                    self.results.append(TestResult(
                                        "3. Review & Auto-Import", False,
                                        f"Status is {new_status}, expected 'imported'", duration
                                    ))
                    else:
                        duration = (datetime.now() - start).total_seconds() * 1000
                        self.results.append(TestResult(
                            "3. Review & Import", False,
                            f"Review failed with status {resp.status}", duration
                        ))
        except Exception as e:
            duration = (datetime.now() - start).total_seconds() * 1000
            self.results.append(TestResult("3. Review & Import", False, str(e), duration))
    
    async def test_search(self):
        """Test knowledge base search"""
        import aiohttp
        
        if not self.token:
            self.results.append(TestResult("4. Search Knowledge Base", False, "No token"))
            return
        
        start = datetime.now()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{TEST_CONFIG['api_base_url']}/api/legal/search",
                    headers=self._headers(json_content=True),
                    json={
                        "query": "hộ tịch",
                        "limit": 5,
                        "include_trace": False,
                    },
                    timeout=aiohttp.ClientTimeout(total=90)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        results = data.get("results", [])
                        duration = (datetime.now() - start).total_seconds() * 1000
                        self.results.append(TestResult(
                            "4. Search Knowledge Base", True,
                            f"Found {len(results)} results", duration
                        ))
                    else:
                        duration = (datetime.now() - start).total_seconds() * 1000
                        self.results.append(TestResult(
                            "4. Search Knowledge Base", False,
                            f"Status {resp.status}", duration
                        ))
        except Exception as e:
            duration = (datetime.now() - start).total_seconds() * 1000
            self.results.append(TestResult("4. Search Knowledge Base", False, str(e), duration))
    
    async def test_chat(self):
        """Test chat with knowledge base"""
        import aiohttp
        
        if not self.token:
            self.results.append(TestResult("5. Chat Ask", False, "No token"))
            return
        
        start = datetime.now()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{TEST_CONFIG['api_base_url']}/api/search/ask/simple",
                    headers=self._headers(json_content=True),
                    json={
                        "question": "Làm thủ tục khai sinh cần những gì?",
                        "role": "admin",
                        "show_rag_trace": True
                    },
                    timeout=aiohttp.ClientTimeout(total=120)  # Longer for LLM
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        answer = data.get("answer", "")
                        has_citation = bool(data.get("citations"))
                        
                        duration = (datetime.now() - start).total_seconds() * 1000
                        msg = f"Got answer ({len(answer)} chars)"
                        if has_citation:
                            msg += " with citations"
                        self.results.append(TestResult(
                            "5. Chat Ask", True, msg, duration
                        ))
                    else:
                        duration = (datetime.now() - start).total_seconds() * 1000
                        self.results.append(TestResult(
                            "5. Chat Ask", False,
                            f"Status {resp.status}", duration
                        ))
        except Exception as e:
            duration = (datetime.now() - start).total_seconds() * 1000
            self.results.append(TestResult("5. Chat Ask", False, str(e), duration))
    
    def print_summary(self):
        """Print test summary"""
        print()
        print("=" * 60)
        print("TEST RESULTS SUMMARY")
        print("=" * 60)
        
        passed = sum(1 for r in self.results if r.passed)
        total = len(self.results)
        
        for result in self.results:
            print(result)
        
        print()
        print(f"Total: {passed}/{total} tests passed")
        
        if passed == total:
            print("All tests PASSED!")
            return 0
        else:
            print(f"{total - passed} test(s) FAILED")
            return 1


async def main():
    """Main entry point"""
    test = E2ETest()
    await test.run_all()
    
    # Exit with appropriate code
    passed = sum(1 for r in test.results if r.passed)
    total = len(test.results)
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    asyncio.run(main())
