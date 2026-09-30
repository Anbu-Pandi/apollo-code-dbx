def get_sample_emails():
    """Return a list of sample emails for testing topic crowding detection."""

    return [
        """
        Date: Mon, 20 Oct 2024 09:15:00 -0400
        Subject: AI Semiconductor Supply Chain Analysis

        Teams,

        Following up on recent semiconductor trends. TSMC and Samsung are ramping up AI chip production. 
        Key points:
        - High demand for AI training chips
        - Memory manufacturers increasing capacity
        - Supply constraints in advanced nodes

        Worth monitoring NVIDIA, AMD, and Intel's positions in this space.
        """,

        """
        Date: Mon, 20 Oct 2024 14:30:00 -0400
        Subject: Re: Japanese Banking Sector Update

        Interesting developments in Japanese banks:
        - BOJ maintaining yield curve control
        - Major banks showing strong earnings
        - Credit growth picking up

        Mitsubishi UFJ and Sumitomo looking particularly interesting here.
        """,

        """
        Date: Tue, 21 Oct 2024 08:45:00 -0400
        Subject: Semiconductor Supply Chain Deep Dive

        Deep dive on semiconductor equipment makers:
        - ASML seeing strong EUV demand
        - Applied Materials expanding capacity
        - Lam Research focused on memory tools

        AI chip demand driving significant capex cycles.
        """,

        """
        Date: Tue, 21 Oct 2024 11:20:00 -0400
        Subject: Clean Energy Infrastructure

        Notable developments in clean energy:
        - Solar panel efficiency improvements
        - Battery storage costs declining
        - Grid infrastructure upgrades

        Keep an eye on First Solar and Enphase Energy.
        """,

        """
        Date: Wed, 22 Oct 2024 09:30:00 -0400
        Subject: AI Chip Demand Analysis

        Following up on AI semiconductor space:
        - Cloud providers increasing AI infrastructure
        - New training models requiring more compute
        - Memory bandwidth becoming bottleneck

        NVIDIA remains key beneficiary, but watch AMD's MI300.
        """,

        """
        Date: Wed, 22 Oct 2024 15:45:00 -0400
        Subject: European Luxury Sector

        Luxury market observations:
        - Chinese demand recovering
        - Tourism picking up in Europe
        - Digital sales channels growing

        LVMH and Hermes showing resilience.
        """,

        """
        Date: Thu, 23 Oct 2024 10:15:00 -0400
        Subject: Memory Chip Market Update

        Memory market dynamics:
        - AI driving HBM demand
        - Server DRAM pricing firming
        - Inventory levels normalizing

        Samsung and Micron key players to watch.
        """,

        """
        Date: Thu, 23 Oct 2024 14:30:00 -0400
        Subject: Clean Energy Policy Developments

        Updates on clean energy initiatives:
        - New tax incentives announced
        - Grid modernization plans
        - Energy storage mandates

        Positive for utilities and component makers.
        """,

        """
        Date: Fri, 24 Oct 2024 09:00:00 -0400
        Subject: AI Chip Supply Chain Alert

        URGENT: Significant developments in AI chip space
        - Major cloud provider doubling AI infrastructure orders
        - TSMC prioritizing AI chip production
        - Memory makers shifting capacity to HBM

        Implications for entire semiconductor supply chain.
        """,

        """
        Date: Fri, 24 Oct 2024 11:45:00 -0400
        Subject: Japanese Financials Follow-up

        Quick update on Japanese banks:
        - Deposit outflows stabilizing
        - Investment banking revenues strong
        - Regional bank consolidation continuing

        Sector rotation into financials continuing.
        """,

        """
        Date: Fri, 24 Oct 2024 15:30:00 -0400
        Subject: Weekend AI Semiconductor Summary

        Week's key developments in AI chips:
        - Multiple cloud providers raising capex
        - Memory supply remains tight
        - New AI models driving demand

        Semiconductor space increasingly crowded.
        """,

        """
        Date: Mon, 27 Oct 2024 08:30:00 -0400
        Subject: Luxury Market China Data

        Fresh data on luxury market:
        - Golden Week sales strong
        - Online penetration increasing
        - Youth demographic growing

        Continue to favor LVMH exposure.
        """,

        """
        Date: Mon, 27 Oct 2024 13:15:00 -0400
        Subject: Semiconductor Equipment Orders

        Breaking: Major order activity
        - ASML received multiple EUV orders
        - Applied Materials guide up
        - Testing equipment demand surge

        AI buildout accelerating.
        """,

        """
        Date: Tue, 28 Oct 2024 09:45:00 -0400
        Subject: Energy Storage Deep Dive

        Battery technology developments:
        - New chemistry breakthroughs
        - Manufacturing scale improving
        - Grid applications expanding

        Opportunities in battery supply chain.
        """
    ]


if __name__ == "__main__":
    # Example usage
    from topic_crowding import TopicCrowdingDetector

    # Get sample data
    emails = get_sample_emails()

    # Initialize detector
    detector = TopicCrowdingDetector(num_topics=5)

    # Analyze emails
    results = detector.analyze(emails)

    # Print results
    print("\nTop Crowded Topics:")
    print(results[['topic_keywords', 'crowding_score', 'mention_velocity']])