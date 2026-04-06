 Multi-criteria search

  - "I need a flight from SFO to Ho Chi Minh City on April 10 that arrives before 8 AM. What are my options?"
  (Should find: Cathay Pacific arr 07:00 $887, Vietnam Airlines arr 03:30 $1098, Japan Airlines arr 03:30 $791)
  - "Find me the cheapest flight from San Francisco to Sydney this week, and a hotel there rated at least 3.5 stars under $115 per night"
  (Should cross-reference flights + hotels together)

  Comparison

  - "Compare all flights from SFO to Hanoi on April 9 -- which one is cheapest and which arrives earliest?"
  - "I'm deciding between Melbourne and Sydney. Which city has better-rated hotels under $110/night?"
  (Melbourne has several 4.0-rated options at $99-$108; Sydney has London Gate Backpackers at 4.6 for $102)

  Date flexibility

  - "What's the cheapest day to fly from SFO to Sydney between April 7 and 13?"
  (Apr 7 Qantas at $826 is cheapest across all dates)
  - "I want to fly to Vietnam on the 11th or 12th. Show me prices for both SGN and HAN"

  Nonstop filter

  - "Are there any nonstop flights from SFO to Australia?"
  (Only Qantas SFO->SYD is nonstop. SFO->MEL and SFO->BNE are all 1-stop)

  Budget planning

  - "I have a $1500 total budget for flights and 4 nights hotel in Tokyo. What can I get on April 8?"
  (No SFO->NRT flights exist though -- should trigger "no data" + alternatives)

  Should trigger "no data" + similar recommendations

  - "Find me flights from Sydney to Saigon next Tuesday"
  (No SYD->SGN route -- should suggest available SFO->SGN instead)
  - "Hotels in Bangkok on April 10"
  (No Bangkok hotel data -- should suggest Hanoi, HCMC, etc.)
  - "Flights from Ho Chi Minh City to Tokyo on April 9"
  (No SGN->NRT route -- should show what routes exist)

  Follow-up chains (tests conversation context)

  1. "Show me flights from SFO to Melbourne on April 10"
  2. "Any of those under $1200?"
  3. "What hotels are available there?"
  4. "Which one has the best rating?"