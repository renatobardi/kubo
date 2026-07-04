/**
 * The signature flat card — defined by a 1px ring (foreground/10), no shadow, no border. rounded-2xl.
 * @startingPoint section="Surfaces" subtitle="Flat ring card + header/content/footer" viewport="700x260"
 */
export interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  /** default = py-24 gap-24 · sm = py-16 gap-16 */
  size?: 'default' | 'sm';
}
export function CardHeader(props: React.HTMLAttributes<HTMLDivElement>): JSX.Element;
export function CardTitle(props: React.HTMLAttributes<HTMLHeadingElement>): JSX.Element;
export function CardDescription(props: React.HTMLAttributes<HTMLParagraphElement>): JSX.Element;
export function CardAction(props: React.HTMLAttributes<HTMLDivElement>): JSX.Element;
export function CardContent(props: React.HTMLAttributes<HTMLDivElement>): JSX.Element;
export function CardFooter(props: React.HTMLAttributes<HTMLDivElement>): JSX.Element;
