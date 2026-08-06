import argparse
import logging
import sys
import warnings

from deepsearcher.configuration import Configuration, init_config
from deepsearcher.offline_loading import load_from_local_files, load_from_website
from deepsearcher.online_query import query
from deepsearcher.utils import log

httpx_logger = logging.getLogger("httpx")  # disable openai's logger output
httpx_logger.setLevel(logging.WARNING)


warnings.simplefilter(action="ignore", category=FutureWarning)  # disable warning output


def main():
    """
    Main entry point for the DeepSearcher CLI.

    This function parses command line arguments and executes the appropriate action
    based on the subcommand provided (query or load). It handles the deprecated
    command line format and provides helpful error messages.

    Returns:
        None
    """
    if "--query" in sys.argv or "--load" in sys.argv:
        print("\033[91m[Deprecated]\033[0m The use of '--query' and '--load' is deprecated.")
        print("Please use:")
        print("  deepsearcher query <your_query> --max_iter 3")
        print(
            "  deepsearcher load <your_local_path_or_url> --collection_name <your_collection_name> --collection_desc <your_collection_description>"
        )
        sys.exit(1)

    config = Configuration()  # Customize your config here
    init_config(config=config)

    parser = argparse.ArgumentParser(prog="deepsearcher", description="Deep Searcher.")
    subparsers = parser.add_subparsers(dest="subcommand", title="subcommands")

    ## Arguments of query
    query_parser = subparsers.add_parser("query", help="Query a question or search topic.")
    query_parser.add_argument("query", type=str, default="", help="query question or search topic.")
    query_parser.add_argument(
        "--max_iter",
        type=int,
        default=3,
        help="Max iterations of reflection. Default is 3.",
    )
    query_parser.add_argument(
        "--collection_name",
        type=str,
        default=None,
        help="Query one explicit collection or activated collection alias.",
    )

    ## Arguments of loading
    load_parser = subparsers.add_parser(
        "load", help="Load knowledge from local files or from URLs."
    )
    load_parser.add_argument(
        "load_path",
        type=str,
        nargs="+",  # 1 or more files or urls
        help="Load knowledge from local files or from URLs.",
    )
    load_parser.add_argument(
        "--batch_size",
        type=int,
        default=256,
        help="Batch size for loading knowledge.",
    )
    load_parser.add_argument(
        "--collection_name",
        type=str,
        default=None,
        help="Destination collection name of loaded knowledge.",
    )
    load_parser.add_argument(
        "--collection_desc",
        type=str,
        default=None,
        help="Description of the collection.",
    )
    load_parser.add_argument(
        "--force_new_collection",
        "--force-new-collection",
        action="store_true",
        help=(
            "Build a versioned collection and safely activate it. "
            "The previous collection is retained for rollback."
        ),
    )

    args = parser.parse_args()
    if args.subcommand == "query":
        query_kwargs = {}
        if args.collection_name:
            query_kwargs["collection_names"] = [args.collection_name]
        final_answer, refs, consumed_tokens = query(
            args.query,
            max_iter=args.max_iter,
            **query_kwargs,
        )
        log.color_print("\n==== FINAL ANSWER====\n")
        log.color_print(final_answer)
        log.color_print("\n### References\n")
        for i, ref in enumerate(refs):
            log.color_print(f"{i + 1}. {ref.text[:60]}… {ref.reference}")
    elif args.subcommand == "load":
        urls = [url for url in args.load_path if url.startswith("http")]
        local_files = [file for file in args.load_path if not file.startswith("http")]
        if args.force_new_collection and urls and local_files:
            parser.error(
                "safe version activation accepts either URLs or local files in one load command"
            )
        kwargs = {}
        if args.collection_name:
            kwargs["collection_name"] = args.collection_name
        if args.collection_desc:
            kwargs["collection_description"] = args.collection_desc
        if args.force_new_collection:
            kwargs["force_new_collection"] = args.force_new_collection
        if args.batch_size:
            kwargs["batch_size"] = args.batch_size
        if len(urls) > 0:
            result = load_from_website(urls, **kwargs)
            log.color_print(f"Collection load result: {result}")
        if len(local_files) > 0:
            result = load_from_local_files(local_files, **kwargs)
            log.color_print(f"Collection load result: {result}")
    else:
        print("Please provide a query or a load argument.")


if __name__ == "__main__":
    main()
