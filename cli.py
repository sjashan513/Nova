import sys


def main():
    print("Nova CLI v1.0 - Initialized (Phase 0)")
    print("Type 'exit' or 'quit' to terminate.")

    while True:
        try:
            user_input = input("\nNova> ").strip()

            if not user_input:
                continue

            command = user_input.lower()

            if command in ['exit', 'quit']:
                print("Shutting down systems. See you next time!")
                break

            if command == 'help':
                print("Basic commands:")
                print("  exit - Close the application")
                print("  help - Show this message")
                print("  [text] - Request for the Planner (Phase 1)")
                continue

            print(f"Processing: {user_input}")
            print(
                "-> [Notice] Planner not connected. This will be implemented in Phase 1.")

        except KeyboardInterrupt:
            print("\nForced shutdown detected. Closing...")
            break
        except Exception as e:
            print(f"Error in main loop: {e}")


if __name__ == "__main__":
    main()
