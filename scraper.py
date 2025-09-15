#!/usr/bin/env python3
"""
AI Competitor Tracker
Web scraper for monitoring AI companies and generating competitive intelligence reports.
"""

import json
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import time
import os
from urllib.parse import urljoin, urlparse
import logging
import feedparser
import re
from dateutil import parser as date_parser

class CompetitorScraper:
    def __init__(self, config_file='config.json'):
        """Initialize the scraper with configuration."""
        with open(config_file, 'r') as f:
            self.config = json.load(f)

        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': self.config['scraping']['user_agent']
        })

        self.setup_logging()

    def setup_logging(self):
        """Set up logging configuration."""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler('scraper.log'),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)

    def scrape_competitor(self, competitor_key):
        """Scrape a single competitor's website."""
        competitor = self.config['competitors'][competitor_key]
        articles = []

        self.logger.info(f"Scraping {competitor['name']}...")

        # Try RSS first (more reliable and includes recent articles)
        if 'rss_url' in competitor:
            try:
                rss_articles = self.scrape_rss(competitor['rss_url'], competitor)
                articles.extend(rss_articles)
                self.logger.info(f"Found {len(rss_articles)} articles from RSS feed")
            except Exception as e:
                self.logger.error(f"Error scraping RSS {competitor['rss_url']}: {str(e)}")

        # Fallback to web scraping if RSS didn't work or didn't get enough articles
        if len(articles) < 5:
            urls_to_scrape = [competitor['blog_url'], competitor.get('news_url')]

            for url in urls_to_scrape:
                if not url:
                    continue
                try:
                    web_articles = self.scrape_url(url, competitor)
                    articles.extend(web_articles)
                    time.sleep(self.config['scraping']['delay_between_requests'])
                except Exception as e:
                    self.logger.error(f"Error scraping {url}: {str(e)}")

        # Filter for recent articles (last 30 days)
        recent_articles = self.filter_recent_articles(articles)
        return recent_articles[:self.config['scraping']['max_articles_per_site']]

    def scrape_rss(self, rss_url, competitor):
        """Scrape articles from RSS feed."""
        articles = []

        try:
            self.logger.info(f"Fetching RSS feed: {rss_url}")
            feed = feedparser.parse(rss_url)

            if feed.bozo:
                self.logger.warning(f"RSS feed may have issues: {rss_url}")

            for entry in feed.entries[:10]:  # Limit to 10 most recent
                try:
                    # Extract article data from RSS entry
                    title = entry.title
                    content = self.get_rss_content(entry)
                    url = entry.link
                    date = self.parse_rss_date(entry)

                    if title and content:
                        articles.append({
                            'title': title,
                            'content': content[:500] + '...' if len(content) > 500 else content,
                            'url': url,
                            'date': date,
                            'company': competitor['name'],
                            'source': 'RSS'
                        })

                except Exception as e:
                    self.logger.error(f"Error processing RSS entry: {str(e)}")

        except Exception as e:
            self.logger.error(f"Error parsing RSS feed {rss_url}: {str(e)}")

        return articles

    def get_rss_content(self, entry):
        """Extract content from RSS entry."""
        content = ""

        # Try different content fields
        if hasattr(entry, 'content') and entry.content:
            content = entry.content[0].value
        elif hasattr(entry, 'summary') and entry.summary:
            content = entry.summary
        elif hasattr(entry, 'description') and entry.description:
            content = entry.description

        # Clean HTML tags
        if content:
            soup = BeautifulSoup(content, 'html.parser')
            content = soup.get_text(strip=True)

        return content

    def parse_rss_date(self, entry):
        """Parse date from RSS entry."""
        try:
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                return datetime(*entry.published_parsed[:6]).strftime('%Y-%m-%d')
            elif hasattr(entry, 'published') and entry.published:
                return date_parser.parse(entry.published).strftime('%Y-%m-%d')
            elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
                return datetime(*entry.updated_parsed[:6]).strftime('%Y-%m-%d')
            elif hasattr(entry, 'updated') and entry.updated:
                return date_parser.parse(entry.updated).strftime('%Y-%m-%d')
        except Exception as e:
            self.logger.error(f"Error parsing RSS date: {str(e)}")

        return datetime.now().strftime('%Y-%m-%d')

    def filter_recent_articles(self, articles):
        """Filter articles to only include recent ones (last 30 days)."""
        recent_articles = []
        cutoff_date = datetime.now() - timedelta(days=30)

        for article in articles:
            try:
                article_date = date_parser.parse(article['date'])
                if article_date >= cutoff_date:
                    recent_articles.append(article)
            except Exception as e:
                # If we can't parse the date, include it anyway
                self.logger.warning(f"Could not parse date '{article['date']}': {str(e)}")
                recent_articles.append(article)

        return sorted(recent_articles, key=lambda x: x.get('date', ''), reverse=True)

    def scrape_url(self, url, competitor):
        """Scrape articles from a specific URL."""
        articles = []

        try:
            response = self.session.get(
                url,
                timeout=self.config['scraping']['timeout']
            )
            response.raise_for_status()

            soup = BeautifulSoup(response.content, 'html.parser')

            # Find article links
            article_links = self.find_article_links(soup, url)

            for link in article_links[:5]:  # Limit to 5 articles per URL
                try:
                    article = self.scrape_article(link, competitor)
                    if article:
                        articles.append(article)
                    time.sleep(self.config['scraping']['delay_between_requests'])
                except Exception as e:
                    self.logger.error(f"Error scraping article {link}: {str(e)}")

        except Exception as e:
            self.logger.error(f"Error accessing {url}: {str(e)}")

        return articles

    def find_article_links(self, soup, base_url):
        """Find article links on a page."""
        links = []

        # Look for common article link patterns
        selectors = [
            'article a[href]',
            '.post a[href]',
            '.entry a[href]',
            'h2 a[href]',
            'h3 a[href]',
            '.title a[href]'
        ]

        for selector in selectors:
            elements = soup.select(selector)
            for element in elements:
                href = element.get('href')
                if href:
                    full_url = urljoin(base_url, href)
                    if self.is_valid_article_url(full_url, base_url):
                        links.append(full_url)

        return list(set(links))  # Remove duplicates

    def is_valid_article_url(self, url, base_url):
        """Check if URL is a valid article URL."""
        parsed_url = urlparse(url)
        parsed_base = urlparse(base_url)

        # Must be from same domain
        if parsed_url.netloc != parsed_base.netloc:
            return False

        # Skip certain paths
        skip_patterns = ['#', 'mailto:', 'tel:', '/tag/', '/category/', '/author/']
        return not any(pattern in url.lower() for pattern in skip_patterns)

    def scrape_article(self, url, competitor):
        """Scrape a single article."""
        try:
            response = self.session.get(url, timeout=self.config['scraping']['timeout'])
            response.raise_for_status()

            soup = BeautifulSoup(response.content, 'html.parser')

            # Extract article data
            title = self.extract_text(soup, competitor['selectors']['title'])
            content = self.extract_text(soup, competitor['selectors']['content'])
            date = self.extract_date(soup, competitor['selectors']['date'])

            if title and content:
                return {
                    'title': title,
                    'content': content[:500] + '...' if len(content) > 500 else content,
                    'url': url,
                    'date': date,
                    'company': competitor['name']
                }

        except Exception as e:
            self.logger.error(f"Error scraping article {url}: {str(e)}")

        return None

    def extract_text(self, soup, selectors):
        """Extract text using CSS selectors."""
        for selector in selectors.split(', '):
            element = soup.select_one(selector)
            if element:
                return element.get_text(strip=True)
        return None

    def extract_date(self, soup, selectors):
        """Extract date from article."""
        for selector in selectors.split(', '):
            element = soup.select_one(selector)
            if element:
                date_text = element.get_text(strip=True)
                if element.get('datetime'):
                    date_text = element.get('datetime')

                # Try to parse date
                try:
                    # This is a simple date extraction - could be improved
                    return date_text
                except:
                    continue

        return datetime.now().strftime('%Y-%m-%d')

    def generate_report(self, all_articles):
        """Generate a markdown report."""
        today = datetime.now().strftime(self.config['reports']['date_format'])
        report_filename = f"reports/ai_competitor_report_{today}.md"

        with open(report_filename, 'w', encoding='utf-8') as f:
            f.write(f"# AI Competitor Intelligence Report - {today}\n\n")
            f.write(f"Generated on {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")

            # Group articles by company
            by_company = {}
            for article in all_articles:
                company = article['company']
                if company not in by_company:
                    by_company[company] = []
                by_company[company].append(article)

            # Write report sections
            for company, articles in by_company.items():
                f.write(f"## {company}\n\n")
                f.write(f"Found {len(articles)} recent articles:\n\n")

                for article in articles:
                    f.write(f"### {article['title']}\n\n")
                    f.write(f"**Date:** {article['date']}\n\n")
                    f.write(f"**URL:** {article['url']}\n\n")
                    f.write(f"**Summary:** {article['content']}\n\n")
                    f.write("---\n\n")

        self.logger.info(f"Report generated: {report_filename}")
        return report_filename

    def run(self):
        """Run the full scraping process."""
        self.logger.info("Starting AI competitor tracking...")

        all_articles = []

        for competitor_key in self.config['competitors'].keys():
            try:
                articles = self.scrape_competitor(competitor_key)
                all_articles.extend(articles)
                self.logger.info(f"Found {len(articles)} articles from {competitor_key}")
            except Exception as e:
                self.logger.error(f"Error processing {competitor_key}: {str(e)}")

        if all_articles:
            report_file = self.generate_report(all_articles)
            self.logger.info(f"Scraping complete. Total articles: {len(all_articles)}")
            self.logger.info(f"Report saved to: {report_file}")
        else:
            self.logger.warning("No articles found during scraping.")

if __name__ == "__main__":
    scraper = CompetitorScraper()
    scraper.run()